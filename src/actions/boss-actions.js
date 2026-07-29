/*
 * Copyright (C) 2026 Red Hat, Inc.
 * SPDX-License-Identifier: LGPL-2.1-or-later
 */

import { getInstallationStatus, getPendingErrorMessage, getPendingErrorType } from "../apis/boss.js";

export const getInstallationStatusAction = () => {
    return async (dispatch) => {
        const status = await getInstallationStatus();
        return dispatch({
            type: "GET_INSTALLATION_STATUS",
            payload: { status },
        });
    };
};

export const getPendingErrorAction = () => {
    return async (dispatch) => {
        const message = await getPendingErrorMessage();
        const type = await getPendingErrorType();
        return dispatch({
            type: "GET_PENDING_ERROR",
            payload: { message, type },
        });
    };
};
