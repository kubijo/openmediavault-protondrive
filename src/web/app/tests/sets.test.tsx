import { Code, ConnectError, createRouterTransport } from '@connectrpc/connect';
import { TransportProvider } from '@connectrpc/connect-query';
import { MantineProvider } from '@mantine/core';
import { expect, it } from '@rstest/core';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';

import type { SaveSetRequest } from '../src/generated/protondrive_api/v1/control_pb.ts';
import { ControlService } from '../src/generated/protondrive_api/v1/control_pb.ts';
import { BackupSets } from '../src/sets.tsx';

it('keeps the loaded revision after a background refresh so stale edits are rejected', async () => {
    let revision = 'original';
    let paths = ['/srv/original'];
    const saves: SaveSetRequest[] = [];
    const transport = createRouterTransport(router =>
        router.service(ControlService, {
            listSets: () => ({
                revision,
                sets: [
                    { id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', name: 'data', paths, localKeep: 2, remoteKeep: 7 },
                ],
            }),
            listContainers: () => ({}),
            saveSet: request => {
                saves.push(request);
                if (request.revision !== revision) {
                    throw new ConnectError('Backup sets changed; reload before saving', Code.InvalidArgument);
                }
                return { set: request.set };
            },
        }),
    );
    const query = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
    try {
        render(
            <MantineProvider>
                <QueryClientProvider client={query}>
                    <TransportProvider transport={transport}>
                        <BackupSets />
                    </TransportProvider>
                </QueryClientProvider>
            </MantineProvider>,
        );
        fireEvent.click(await screen.findByRole('button', { name: 'Edit' }));
        fireEvent.change(screen.getByRole('textbox', { name: 'Excluded relative paths, one per line' }), {
            target: { value: 'local-edit' },
        });
        revision = 'external-edit';
        paths = ['/srv/externally-added-critical-data'];
        await act(async () => {
            await query.refetchQueries();
        });
        fireEvent.click(screen.getByRole('button', { name: 'Save backup set' }));
        await waitFor(() => expect(saves).toHaveLength(1));
        await screen.findByText(/Backup sets changed; reload before saving/);
        expect(saves[0]?.revision).toBe('original');
        expect(saves[0]?.set?.paths).toEqual(['/srv/original']);
        expect(saves[0]?.set?.exclusions).toEqual(['local-edit']);
        expect(paths).toEqual(['/srv/externally-added-critical-data']);
    } finally {
        cleanup();
        query.clear();
    }
});
