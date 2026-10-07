import '@mantine/core/styles.css';

import { TransportProvider } from '@connectrpc/connect-query';
import { Anchor, AppShell, Group, MantineProvider, NavLink, Stack, Text } from '@mantine/core';
import { QueryClientProvider } from '@tanstack/react-query';
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { createBrowserRouter, Link, Outlet, RouterProvider } from 'react-router';

import { queryClient, transport } from './api.ts';
import { Archive } from './archive.tsx';
import { Backups } from './backups.tsx';
import { JobView } from './job.tsx';
import { Overview } from './overview.tsx';
import { BackupSets } from './sets.tsx';
import { Settings } from './settings.tsx';
import styles from './shell.module.scss';

const router = createBrowserRouter(
    [
        {
            element: (
                <AppShell header={{ height: 64 }} padding="md">
                    <AppShell.Header>
                        <Group className={styles.header} justify="space-between">
                            <Text fw={700}>Proton Drive</Text>
                            <Anchor href="/">OpenMediaVault</Anchor>
                        </Group>
                    </AppShell.Header>
                    <AppShell.Main>
                        <div className={styles.content}>
                            <Stack gap="xs">
                                <NavLink component={Link} to="/" label="Overview" />
                                <NavLink component={Link} to="/settings" label="Settings" />
                                <NavLink component={Link} to="/sets" label="Backup sets" />
                                <NavLink component={Link} to="/backups" label="Remote backups" />
                            </Stack>
                            <Outlet />
                        </div>
                    </AppShell.Main>
                </AppShell>
            ),
            children: [
                { index: true, element: <Overview /> },
                { path: 'jobs/:id', element: <JobView /> },
                { path: 'archives/:id', element: <Archive /> },
                { path: 'settings', element: <Settings /> },
                { path: 'sets', element: <BackupSets /> },
                { path: 'backups', element: <Backups /> },
            ],
        },
    ],
    { basename: '/protondrive' },
);

const root = document.getElementById('root');
if (!root) throw new Error('Application root is missing');
createRoot(root).render(
    <StrictMode>
        <MantineProvider defaultColorScheme="dark" theme={{ defaultRadius: 'xs' }}>
            <QueryClientProvider client={queryClient}>
                <TransportProvider transport={transport}>
                    <RouterProvider router={router} />
                </TransportProvider>
            </QueryClientProvider>
        </MantineProvider>
    </StrictMode>,
);
