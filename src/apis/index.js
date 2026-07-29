/*
 * Copyright (C) 2023 Red Hat, Inc.
 * SPDX-License-Identifier: LGPL-2.1-or-later
 */
import { LocalizationClient } from "./localization.js";
import { NetworkClient } from "./network.js";
import { PayloadsClient } from "./payloads.js";
import { RuntimeClient } from "./runtime.js";
import { StorageClient } from "./storage.js";
import { TimezoneClient } from "./timezone.js";
import { UsersClient } from "./users.js";

// Clients needed on the progress page after installation completes (SUCCEEDED/FAILED).
// Network is needed for bug reporting; Runtime for reboot data.
export const minimalModuleClients = [
    NetworkClient,
    RuntimeClient,
];

export const moduleClients = [
    LocalizationClient,
    NetworkClient,
    PayloadsClient,
    RuntimeClient,
    StorageClient,
    TimezoneClient,
    UsersClient
];
