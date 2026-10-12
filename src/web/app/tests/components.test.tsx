import { Code, ConnectError } from '@connectrpc/connect';
import { MantineProvider } from '@mantine/core';
import { afterEach, describe, expect, it } from '@rstest/core';
import { cleanup, render, screen } from '@testing-library/react';

import { Failure, failureError } from '../src/components.tsx';

afterEach(cleanup);

describe('actionable failures', () => {
    it.each([
        [Code.Unauthenticated, 'OMV login required', 'Sign in to OpenMediaVault'],
        [Code.PermissionDenied, 'Access denied', null],
        [Code.Unavailable, 'Service unavailable', null],
        [Code.ResourceExhausted, 'Service busy', null],
        [Code.DataLoss, 'Invalid archive', null],
        [Code.FailedPrecondition, 'Action required', null],
    ])('shows a distinct title for code %s', (code, title, action) => {
        render(
            <MantineProvider>
                <Failure error={new ConnectError('Safe administrator action', code)} />
            </MantineProvider>,
        );
        expect(screen.getByRole('alert').textContent).toContain(title);
        if (action) expect(screen.getByRole('link', { name: action })).toBeTruthy();
        if (code !== Code.Unauthenticated)
            expect(screen.getByRole('alert').textContent).toContain('Safe administrator action');
    });

    it('distinguishes Proton sign-in from other failed preconditions', () => {
        render(
            <MantineProvider>
                <Failure error={failureError('Sign in to Proton Drive', 'sign_in_required')} />
            </MantineProvider>,
        );
        expect(screen.getByRole('alert').textContent).toContain('Proton sign-in required');
        expect(screen.getByRole('link', { name: 'Open account' })).toBeTruthy();
    });
});
