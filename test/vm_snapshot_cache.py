#!/usr/bin/python3

# Copyright (C) 2026 Red Hat, Inc.
# SPDX-License-Identifier: LGPL-2.1-or-later

import fcntl
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

DEFAULT_CACHE_DIR = os.path.join(
    os.environ.get("XDG_DATA_HOME", os.path.join(Path.home(), ".local", "share")),
    "anaconda-vm-snapshots",
)
VIRSH = ["virsh", "-c", "qemu:///session"]
DOMAIN_PREFIX = "test-"


class VMSnapshotCache:
    def __init__(self, cache_dir=None):
        self.cache_dir = Path(
            cache_dir or os.environ.get("TEST_VM_CACHE_DIR", DEFAULT_CACHE_DIR)
        )
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def compute_cache_key(self, updates_img, firmware, payload_type,
                          memory_mb, image, extra_boot_args=""):
        img_hash = _sha256_file(updates_img)
        identity = f"{img_hash}:{firmware}:{payload_type}:{memory_mb}:{image}:{extra_boot_args}"
        return hashlib.sha256(identity.encode()).hexdigest()[:16]

    def _key_dir(self, key):
        d = self.cache_dir / key
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _slot_id(self, label):
        return label

    def has_snapshot(self, key, label):
        d = self._key_dir(key)
        slot = self._slot_id(label)
        return (d / f"{slot}.save").exists() and (d / f"{slot}.meta").exists()

    def save_snapshot(self, domain_name, key, label, iso_path,
                      ssh_address, ssh_port, web_address, web_port):
        d = self._key_dir(key)
        slot = self._slot_id(label)
        lock_path = d / f"{slot}.lock"
        lock_fd = open(lock_path, "w")
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            lock_fd.close()
            print("VM snapshot: another process is saving, skipping", file=sys.stderr)
            return

        try:
            save_file = d / f"{slot}.save"
            meta_file = d / f"{slot}.meta"

            # Extract kernel+initrd from ISO (shared per key)
            vmlinuz = d / "vmlinuz"
            initrd = d / "initrd.img"
            if not vmlinuz.exists():
                subprocess.run([
                    "osirrox", "-indev", iso_path,
                    "-extract", "/images/pxeboot/vmlinuz", str(vmlinuz),
                    "-extract", "/images/pxeboot/initrd.img", str(initrd),
                ], capture_output=True, check=True)

            # Save VM state
            r = subprocess.run(
                [*VIRSH, "save", domain_name, str(save_file)],
                capture_output=True, text=True,
            )
            if r.returncode != 0:
                raise RuntimeError(f"virsh save failed: {r.stderr.strip()}")

            # Fix kernel/initrd paths in saved XML
            xml = subprocess.run(
                [*VIRSH, "save-image-dumpxml", str(save_file)],
                capture_output=True, text=True, check=True,
            ).stdout
            xml = re.sub(r"<kernel>[^<]*</kernel>", f"<kernel>{vmlinuz}</kernel>", xml)
            xml = re.sub(r"<initrd>[^<]*</initrd>", f"<initrd>{initrd}</initrd>", xml)
            subprocess.run(
                [*VIRSH, "save-image-define", str(save_file), "/dev/stdin"],
                input=xml, text=True, capture_output=True, check=True,
            )

            meta = {
                "key": key,
                "slot": slot,
                "created": time.time(),
                "ssh_address": ssh_address,
                "ssh_port": str(ssh_port),
                "web_address": web_address,
                "web_port": str(web_port),
                "domain_name": domain_name,
            }
            meta_file.write_text(json.dumps(meta, indent=2))

            size_mb = save_file.stat().st_size // (1024 * 1024)
            print(f"VM snapshot: saved {key}/{slot} ({size_mb}M)")

            self._cleanup_old()
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            lock_fd.close()

    def get_metadata(self, key, label):
        d = self._key_dir(key)
        meta_file = d / f"{self._slot_id(label)}.meta"
        return json.loads(meta_file.read_text())

    def restore_snapshot(self, key, label):
        d = self._key_dir(key)
        save_file = d / f"{self._slot_id(label)}.save"

        r = subprocess.run(
            [*VIRSH, "restore", str(save_file), "--paused"],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            raise RuntimeError(f"virsh restore failed: {r.stderr.strip()}")

    def rebind_ports(self, qemu_monitor_fn, meta,
                     new_ssh_address, new_ssh_port,
                     new_web_address, new_web_port):
        old_ssh_addr = meta["ssh_address"]
        old_ssh_port = meta["ssh_port"]
        old_web_addr = meta["web_address"]
        old_web_port = meta["web_port"]

        qemu_monitor_fn(
            f"hostfwd_remove hostnet0 tcp:{old_ssh_addr}:{old_ssh_port}"
        )
        qemu_monitor_fn(
            f"hostfwd_add hostnet0 tcp:{new_ssh_address}:{new_ssh_port}-:22"
        )
        qemu_monitor_fn(
            f"hostfwd_remove hostnet0 tcp:{old_web_addr}:{old_web_port}"
        )
        qemu_monitor_fn(
            f"hostfwd_add hostnet0 tcp:{new_web_address}:{new_web_port}-:80"
        )

    def delete_snapshot(self, key, label=None):
        d = self._key_dir(key)
        if label:
            slot = self._slot_id(label)
            for suffix in (".save", ".meta", ".lock"):
                (d / f"{slot}{suffix}").unlink(missing_ok=True)
        else:
            import shutil
            shutil.rmtree(d, ignore_errors=True)

    def _cleanup_old(self, max_age_hours=48, max_count=10):
        entries = []
        for meta_file in self.cache_dir.glob("*/*.meta"):
            try:
                meta = json.loads(meta_file.read_text())
                save_file = meta_file.with_suffix(".save")
                if save_file.exists():
                    entries.append((meta["key"], meta["slot"], meta["created"], meta_file))
            except (json.JSONDecodeError, KeyError):
                continue

        entries.sort(key=lambda e: e[2], reverse=True)
        now = time.time()

        for key, slot, created, _ in entries:
            age_hours = (now - created) / 3600
            if age_hours > max_age_hours:
                self.delete_snapshot(key, slot)
                print(f"VM snapshot: evicted {key}/{slot} (age: {age_hours:.0f}h)")

        remaining = [e for e in entries
                     if (self._key_dir(e[0]) / f"{e[1]}.save").exists()]
        for key, slot, _, _ in remaining[max_count:]:
            self.delete_snapshot(key, slot)
            print(f"VM snapshot: evicted {key}/{slot} (count limit)")


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()
