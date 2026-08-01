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
from tempfile import NamedTemporaryFile

DEFAULT_CACHE_DIR = os.path.join(
    os.environ.get("XDG_DATA_HOME", os.path.join(Path.home(), ".local", "share")),
    "anaconda-vm-snapshots",
)
VIRSH = ["virsh", "-c", "qemu:///session"]


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

    def has_snapshot(self, key):
        save = self.cache_dir / f"{key}.save"
        meta = self.cache_dir / f"{key}.meta"
        return save.exists() and meta.exists()

    def save_snapshot(self, domain_name, key, iso_path,
                      ssh_address, ssh_port, web_address, web_port):
        lock_path = self.cache_dir / f"{key}.lock"
        lock_fd = open(lock_path, "w")
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            lock_fd.close()
            print("VM snapshot: another process is saving, skipping", file=sys.stderr)
            return

        try:
            save_file = self.cache_dir / f"{key}.save"
            meta_file = self.cache_dir / f"{key}.meta"

            # Extract kernel+initrd from ISO for persistence
            vmlinuz = self.cache_dir / f"{key}.vmlinuz"
            initrd = self.cache_dir / f"{key}.initrd.img"
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

            # Extract and fix XML (replace temp kernel/initrd paths)
            xml = subprocess.run(
                [*VIRSH, "save-image-dumpxml", str(save_file)],
                capture_output=True, text=True, check=True,
            ).stdout
            xml = re.sub(r"<kernel>[^<]*</kernel>", f"<kernel>{vmlinuz}</kernel>", xml)
            xml = re.sub(r"<initrd>[^<]*</initrd>", f"<initrd>{initrd}</initrd>", xml)

            # Write fixed XML back to save file
            subprocess.run(
                [*VIRSH, "save-image-define", str(save_file), "/dev/stdin"],
                input=xml, text=True, capture_output=True, check=True,
            )

            # Store metadata
            meta = {
                "key": key,
                "created": time.time(),
                "original_ssh_address": ssh_address,
                "original_ssh_port": ssh_port,
                "original_web_address": web_address,
                "original_web_port": web_port,
                "domain_name": domain_name,
            }
            meta_file.write_text(json.dumps(meta, indent=2))
            print(f"VM snapshot: saved as {key} ({save_file.stat().st_size // (1024*1024)}M)")

            self._cleanup_old()
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            lock_fd.close()

    def get_metadata(self, key):
        meta_file = self.cache_dir / f"{key}.meta"
        return json.loads(meta_file.read_text())

    def restore_snapshot(self, key, new_label, console_file=None):
        save_file = self.cache_dir / f"{key}.save"

        # Load and modify XML template
        xml = subprocess.run(
            [*VIRSH, "save-image-dumpxml", str(save_file)],
            capture_output=True, text=True, check=True,
        ).stdout
        xml = re.sub(r"<name>[^<]*</name>", f"<name>{new_label}</name>", xml)
        if console_file:
            xml = re.sub(
                r"(<serial type=['\"]file['\"]>.*?<source path=)['\"][^'\"]*['\"]",
                rf"\1'{console_file}'",
                xml,
                flags=re.DOTALL,
            )

        # Write modified XML to temp file and restore paused
        with NamedTemporaryFile(mode="w", suffix=".xml", delete=False) as f:
            f.write(xml)
            tmp_xml = f.name

        try:
            r = subprocess.run(
                [*VIRSH, "restore", str(save_file), "--xml", tmp_xml, "--paused"],
                capture_output=True, text=True,
            )
            if r.returncode != 0:
                raise RuntimeError(f"virsh restore failed: {r.stderr.strip()}")
        finally:
            os.unlink(tmp_xml)

    def rebind_ports(self, qemu_monitor_fn, meta,
                     new_ssh_address, new_ssh_port,
                     new_web_address, new_web_port):
        old_ssh_addr = meta["original_ssh_address"]
        old_ssh_port = meta["original_ssh_port"]
        old_web_addr = meta["original_web_address"]
        old_web_port = meta["original_web_port"]

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

    def delete_snapshot(self, key):
        for suffix in (".save", ".meta", ".vmlinuz", ".initrd.img", ".lock"):
            f = self.cache_dir / f"{key}{suffix}"
            f.unlink(missing_ok=True)

    def _cleanup_old(self, max_age_hours=48, max_count=5):
        entries = []
        for meta_file in self.cache_dir.glob("*.meta"):
            try:
                meta = json.loads(meta_file.read_text())
                entries.append((meta["key"], meta["created"]))
            except (json.JSONDecodeError, KeyError):
                continue

        entries.sort(key=lambda e: e[1], reverse=True)
        now = time.time()

        for key, created in entries:
            age_hours = (now - created) / 3600
            if age_hours > max_age_hours:
                self.delete_snapshot(key)
                print(f"VM snapshot: evicted {key} (age: {age_hours:.0f}h)")

        # Keep only max_count newest
        remaining = [e for e in entries if (self.cache_dir / f"{e[0]}.save").exists()]
        for key, _ in remaining[max_count:]:
            self.delete_snapshot(key)
            print(f"VM snapshot: evicted {key} (count limit)")


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()
