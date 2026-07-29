/*
 * Copyright (C) 2022 Red Hat, Inc.
 * SPDX-License-Identifier: LGPL-2.1-or-later
 */

import cockpit from "cockpit";

import { getInstallationStatusAction, getPendingErrorAction } from "../actions/boss-actions.js";

import { error } from "../helpers/log.js";
import { _callClient, _getProperty } from "./helpers.js";

import { moduleClients } from "./index.js";
import { NetworkClient } from "./network.js";
import { RuntimeClient } from "./runtime.js";

const OBJECT_PATH = "/org/fedoraproject/Anaconda/Boss";
const INTERFACE_NAME = "org.fedoraproject.Anaconda.Boss";

const callClient = (...args) => {
    return _callClient(BossClient, OBJECT_PATH, INTERFACE_NAME, ...args);
};

/**
 * @param {string} address      Anaconda bus address
 * @param {Function} dispatch   Redux dispatch function
 *
 * @returns {Object}            A DBus client for the Boss bus
 */
export class BossClient {
    constructor (address, dispatch) {
        if (BossClient.instance && (!address || BossClient.instance.address === address)) {
            return BossClient.instance;
        }

        BossClient.instance?.client.close();

        BossClient.instance = this;

        this.client = cockpit.dbus(
            INTERFACE_NAME,
            { address, bus: "none", superuser: "try" }
        );
        this.address = address;
        this.dispatch = dispatch;
    }

    async init (args = {}, { completed = false } = {}) {
        this.client.addEventListener("close", () => error("Boss client closed"));

        await this.dispatch(getInstallationStatusAction());
        await this.dispatch(getPendingErrorAction());
        this.startEventMonitor();

        // On reconnection to a finished installation (SUCCEEDED/FAILED), skip
        // heavy module init (Storage, Payloads, etc.) — their stale state can
        // cause crashes (e.g. UnknownCompsEnvironmentError from outdated comps data).
        const clients = completed ? [NetworkClient, RuntimeClient] : moduleClients;

        await Promise.all(
            clients.map(Client => new Client(this.address, this.dispatch).init(args))
        );
    }

    startEventMonitor () {
        this._subscription = this.client.subscribe(
            { },
            (path, iface, signal, args) => {
                if (signal !== "PropertiesChanged" || args[0] !== INTERFACE_NAME) {
                    return;
                }

                if (Object.hasOwn(args[1], "InstallationStatus")) {
                    this.dispatch(getInstallationStatusAction());
                }
                if (Object.hasOwn(args[1], "PendingErrorMessage")) {
                    this.dispatch(getPendingErrorAction());
                }
                if (Object.hasOwn(args[1], "ActiveInstallationTask") &&
                    args[1].ActiveInstallationTask.v) {
                    for (const Client of moduleClients) {
                        Client.instance?.stopEventMonitor();
                    }
                }
            }
        );
    }

    stopEventMonitor () {
        this._subscription?.remove();
    }
}

/**
 * @param {string} task         DBus path to a task
 *
 * @returns {Promise}           Resolves the total number of tasks
 */
export const getSteps = ({ task }) => {
    return new BossClient().client.call(
        task,
        "org.freedesktop.DBus.Properties",
        "Get",
        ["org.fedoraproject.Anaconda.Task", "Steps"]
    )
            .then(ret => ret[0]);
};

/**
 * @returns {Promise}           Resolves the object path of the active installation task, or ""
 */
export const getActiveInstallationTask = () => {
    return _getProperty(BossClient, OBJECT_PATH, INTERFACE_NAME, "ActiveInstallationTask");
};

/**
 * @returns {Promise}           Resolves a list of tasks
 */
export const installWithTasks = () => {
    return callClient("InstallWithTasks", []);
};

/**
 * @param {string} locale       Locale id
 */
export const setLocale = ({ locale }) => {
    return callClient("SetLocale", [locale]);
};

/**
 * @returns {Promise}           Resolves the installation status enum value
 */
export const getInstallationStatus = () => {
    return _getProperty(BossClient, OBJECT_PATH, INTERFACE_NAME, "InstallationStatus");
};

/**
 * @returns {Promise}           Resolves the pending error message, or ""
 */
export const getPendingErrorMessage = () => {
    return _getProperty(BossClient, OBJECT_PATH, INTERFACE_NAME, "PendingErrorMessage");
};

/**
 * @returns {Promise}           Resolves the pending error type
 */
export const getPendingErrorType = () => {
    return _getProperty(BossClient, OBJECT_PATH, INTERFACE_NAME, "PendingErrorType");
};
