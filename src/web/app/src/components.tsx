import { Code, ConnectError } from '@connectrpc/connect';
import { Alert, Anchor, Loader } from '@mantine/core';
import type { ReactElement } from 'react';

import { FailureDetailSchema } from './generated/protondrive_api/v1/control_pb.ts';

const failureCodes: Record<string, Code> = {
    failed_precondition: Code.FailedPrecondition,
    sign_in_required: Code.FailedPrecondition,
    unavailable: Code.Unavailable,
    busy: Code.ResourceExhausted,
    invalid_archive: Code.DataLoss,
    internal: Code.Internal,
};

export function failureError(message: string, failureCode: string): ConnectError {
    return new ConnectError(message, failureCodes[failureCode] ?? Code.Unknown, undefined, [
        { desc: FailureDetailSchema, value: { code: failureCode } },
    ]);
}

export function Failure({ error }: { error: Error | null }): ReactElement | null {
    if (!error) return null;
    const code = error instanceof ConnectError ? error.code : Code.Unknown;
    const message = error instanceof ConnectError ? error.rawMessage : error.message;
    const reason = error instanceof ConnectError ? error.findDetails(FailureDetailSchema)[0]?.code : undefined;
    const titles: Partial<Record<Code, string>> = {
        [Code.Unauthenticated]: 'OMV login required',
        [Code.PermissionDenied]: 'Access denied',
        [Code.Unavailable]: 'Service unavailable',
        [Code.ResourceExhausted]: 'Service busy',
        [Code.DataLoss]: 'Invalid archive',
        [Code.FailedPrecondition]: 'Action required',
    };
    const title = reason === 'sign_in_required' ? 'Proton sign-in required' : (titles[code] ?? 'Operation failed');
    return (
        <Alert color="red" title={title} role="alert">
            {code === Code.Unauthenticated ? (
                <Anchor href="/" children="Sign in to OpenMediaVault" />
            ) : (
                <>
                    {message}
                    {reason === 'sign_in_required' && (
                        <>
                            {' '}
                            <Anchor href="/protondrive/" children="Open account" />
                        </>
                    )}
                    {code === Code.Unavailable && <div children="Check the service, then retry." />}
                    {code === Code.ResourceExhausted && <div children="Wait for the current operation, then retry." />}
                    {code === Code.DataLoss && <div children="Do not restore this archive. Try another backup copy." />}
                </>
            )}
        </Alert>
    );
}

export function Loading(): ReactElement {
    return <Loader aria-label="Loading" />;
}
