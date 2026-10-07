import { Code, ConnectError } from '@connectrpc/connect';
import { Alert, Anchor, Loader } from '@mantine/core';
import type { ReactElement } from 'react';

export function Failure({ error }: { error: Error | null }): ReactElement | null {
    if (!error) return null;
    const authentication = error instanceof ConnectError && error.code === Code.Unauthenticated;
    return (
        <Alert color="red" title={authentication ? 'OMV login required' : 'Operation failed'} role="alert">
            {authentication ? <Anchor href="/">Sign in to OpenMediaVault</Anchor> : error.message}
        </Alert>
    );
}

export function Loading(): ReactElement {
    return <Loader aria-label="Loading" />;
}
