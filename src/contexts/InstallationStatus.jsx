/*
 * Copyright (C) 2026 Red Hat, Inc.
 * SPDX-License-Identifier: LGPL-2.1-or-later
 */
import React, { createContext, useContext, useEffect, useState } from "react";

import { BossClient, getInstallationStatus } from "../apis/boss.js";

const BOSS_INTERFACE = "org.fedoraproject.Anaconda.Boss";

export const InstallationStatusContext = createContext(null);

export const useInstallationStatus = () => useContext(InstallationStatusContext);

export const InstallationStatusProvider = ({ children }) => {
    const [status, setStatus] = useState(null);

    useEffect(() => {
        getInstallationStatus().then(setStatus);

        const subscription = new BossClient().client.subscribe(
            { },
            (path, iface, signal, args) => {
                if (signal === "PropertiesChanged" &&
                    args[0] === BOSS_INTERFACE &&
                    Object.hasOwn(args[1], "InstallationStatus")) {
                    setStatus(args[1].InstallationStatus.v);
                }
            }
        );

        return () => subscription.remove();
    }, []);

    return (
        <InstallationStatusContext.Provider value={status}>
            {children}
        </InstallationStatusContext.Provider>
    );
};
