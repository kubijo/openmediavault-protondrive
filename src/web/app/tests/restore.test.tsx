import type { Transport } from '@connectrpc/connect';
import { Code, ConnectError, createRouterTransport } from '@connectrpc/connect';
import { TransportProvider } from '@connectrpc/connect-query';
import { MantineProvider } from '@mantine/core';
import { afterEach, describe, expect, it } from '@rstest/core';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import type { ReactElement } from 'react';
import { createMemoryRouter, RouterProvider } from 'react-router';

import { Archive } from '../src/archive.tsx';
import type { StartOperationRequest } from '../src/generated/protondrive_api/v1/control_pb.ts';
import { ControlService } from '../src/generated/protondrive_api/v1/control_pb.ts';
import { Overview } from '../src/overview.tsx';

const dispose: (() => void)[] = [];
afterEach(() => {
    cleanup();
    for (const close of dispose.splice(0)) close();
});

function show(element: ReactElement, transport: Transport): void {
    const query = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
    const router = createMemoryRouter([{ path: '/archives/:id', element }], {
        initialEntries: ['/archives/cccccccc-cccc-4ccc-8ccc-cccccccccccc'],
    });
    dispose.push(() => {
        router.dispose();
        query.clear();
    });
    render(
        <MantineProvider>
            <QueryClientProvider client={query}>
                <TransportProvider transport={transport}>
                    <RouterProvider router={router} />
                </TransportProvider>
            </QueryClientProvider>
        </MantineProvider>,
    );
}

function deferred(): { promise: Promise<void>; resolve: () => void } {
    let resolve = (): void => {
        throw new Error('Promise constructor did not initialize the resolver');
    };
    const promise = new Promise<void>(complete => {
        resolve = complete;
    });
    return { promise, resolve };
}

describe('restore preview', () => {
    it('does not restore a stale preview after edits made while the request was in flight', async () => {
        const pending = deferred();
        const entered = deferred();
        const transport = createRouterTransport(router =>
            router.service(ControlService, {
                getArchive: () => ({ total: 1, members: [{ path: 'data/file', kind: 'file', size: 7n }] }),
                previewExtraction: async request => {
                    entered.resolve();
                    await pending.promise;
                    return { entries: 1, requiredBytes: 4103n, destination: request.extraction?.destination ?? '' };
                },
            }),
        );
        show(<Archive />, transport);
        fireEvent.click(await screen.findByRole('checkbox', { name: 'data/file' }));
        const destination = screen.getByRole('textbox', { name: 'New extraction directory' });
        fireEvent.change(destination, { target: { value: '/srv/first' } });
        fireEvent.click(screen.getByRole('button', { name: 'Preview extraction' }));
        await entered.promise;
        fireEvent.change(destination, { target: { value: '/srv/changed' } });
        await act(async () => {
            pending.resolve();
            await pending.promise;
        });
        await waitFor(() =>
            expect(screen.getByRole('button', { name: 'Preview extraction' }).hasAttribute('data-loading')).toBe(false),
        );
        expect(screen.queryByRole('button', { name: 'Extract selected files' })).toBeNull();
    });

    it('invalidates a preview when the destination changes', async () => {
        const operations: StartOperationRequest[] = [];
        const transport = createRouterTransport(router =>
            router.service(ControlService, {
                getArchive: () => ({ total: 1, members: [{ path: 'data/file', kind: 'file', size: 7n }] }),
                previewExtraction: request => ({
                    entries: 1,
                    requiredBytes: 4103n,
                    destination: request.extraction?.destination ?? '',
                }),
                startOperation: request => {
                    operations.push(request);
                    return {};
                },
            }),
        );
        show(<Archive />, transport);
        fireEvent.click(await screen.findByRole('checkbox', { name: 'data/file' }));
        const destination = screen.getByRole('textbox', { name: 'New extraction directory' });
        fireEvent.change(destination, { target: { value: '/srv/first' } });
        fireEvent.click(screen.getByRole('button', { name: 'Preview extraction' }));
        await screen.findByRole('heading', { name: 'Extraction preview' });
        expect(screen.getByRole('button', { name: 'Extract selected files' })).toBeTruthy();
        fireEvent.change(destination, { target: { value: '/srv/changed' } });
        await waitFor(() => expect(screen.queryByRole('button', { name: 'Extract selected files' })).toBeNull());
        expect(operations).toHaveLength(0);
    });

    it('never starts extraction when preflight rejects the destination', async () => {
        const operations: StartOperationRequest[] = [];
        const transport = createRouterTransport(router =>
            router.service(ControlService, {
                getArchive: () => ({ total: 1, members: [{ path: 'data/file', kind: 'file', size: 7n }] }),
                previewExtraction: () => {
                    throw new ConnectError('Extraction destination already exists', Code.InvalidArgument);
                },
                startOperation: request => {
                    operations.push(request);
                    return {};
                },
            }),
        );
        show(<Archive />, transport);
        fireEvent.click(await screen.findByRole('checkbox', { name: 'data/file' }));
        fireEvent.change(screen.getByRole('textbox', { name: 'New extraction directory' }), {
            target: { value: '/srv/existing' },
        });
        fireEvent.click(screen.getByRole('button', { name: 'Preview extraction' }));
        await screen.findByText(/Extraction destination already exists/);
        expect(screen.queryByRole('button', { name: 'Extract selected files' })).toBeNull();
        expect(operations).toHaveLength(0);
    });

    it('shows account lookup failures and keeps sign-in available', async () => {
        const transport = createRouterTransport(router =>
            router.service(ControlService, {
                getStatus: () => ({ accountState: 'unknown', accountError: 'Proton lookup failed' }),
            }),
        );
        show(<Overview />, transport);
        await screen.findByText('Proton lookup failed');
        expect(screen.getByRole('button', { name: 'Sign in' }).hasAttribute('disabled')).toBe(false);
    });
});
