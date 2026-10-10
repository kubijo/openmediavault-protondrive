import { useMutation, useQuery } from '@connectrpc/connect-query';
import { Alert, Anchor, Badge, Button, Code, Group, Paper, Progress, Stack, Text, Title } from '@mantine/core';
import type { ReactElement } from 'react';
import { useNavigate } from 'react-router';

import { Failure, failureError, Loading } from './components.tsx';
import { ControlService, Operation } from './generated/protondrive_api/v1/control_pb.ts';

export function Overview(): ReactElement {
    const status = useQuery(ControlService.method.getStatus, {}, { refetchInterval: 3000 });
    const configuration = useQuery(ControlService.method.getConfiguration);
    const operation = useMutation(ControlService.method.startOperation);
    const navigate = useNavigate();
    const start = async (kind: Operation, destinationId = ''): Promise<void> => {
        const result = await operation.mutateAsync({ operation: kind, requestId: crypto.randomUUID(), destinationId });
        if (result.job) await navigate(`/jobs/${result.job.id}`);
    };
    const data = status.data;
    if (!data) return status.error ? <Failure error={status.error} /> : <Loading />;
    const backends = data.backends.length
        ? data.backends
        : configuration.data?.configuration?.destinations.length
          ? configuration.data.configuration.destinations.map(backend => ({
                ...backend,
                state: 'unavailable',
                email: '',
                authenticationUrl: '',
                error: data.accountError || 'Storage service unavailable',
                transferPhase: '',
                transferFile: '',
                transferElapsedSeconds: 0n,
                transferPercent: undefined,
            }))
          : [
                {
                    id: 'protondrive',
                    kind: 'protondrive',
                    name: 'Proton Drive',
                    enabled: true,
                    state: data.accountState,
                    email: data.accountEmail,
                    authenticationUrl: data.authenticationUrl,
                    error: data.accountError,
                    transferPhase: data.transferPhase,
                    transferFile: data.transferFile,
                    transferElapsedSeconds: data.transferElapsedSeconds,
                    transferPercent: undefined,
                },
            ];
    const targetsReady = backends.filter(backend => backend.enabled).every(backend => backend.state === 'signed-in');
    return (
        <Stack>
            <Title order={1} children="Cloud Backup" />
            <Failure error={operation.error} />
            {data.pendingConfiguration && (
                <Alert color="yellow" title="Pending configuration">
                    Saved changes need to be applied before the next backup.
                    <Button
                        onClick={() => {
                            void start(Operation.APPLY_CONFIGURATION).catch(() => undefined);
                        }}
                        children="Apply changes"
                    />
                </Alert>
            )}
            {backends.map(backend => (
                <Paper withBorder p="lg" key={backend.id}>
                    <Stack>
                        <Group justify="space-between">
                            <Title order={2} children={backend.name} />
                            <Badge
                                color={backend.enabled ? undefined : 'gray'}
                                children={backend.enabled ? 'Destination enabled' : 'Destination disabled'}
                            />
                        </Group>
                        <Text children={backend.state === 'signed-in' ? 'Signed in' : backend.state} />
                        {backend.error && (
                            <Alert color="yellow" title="Account status unavailable" children={backend.error} />
                        )}
                        {backend.email && <Text children={backend.email} />}
                        {backend.authenticationUrl && (
                            <Anchor
                                href={backend.authenticationUrl}
                                target="_blank"
                                rel="noopener noreferrer"
                                children="Complete sign-in"
                            />
                        )}
                        <Group>
                            <Button
                                variant="default"
                                disabled={
                                    data.running ||
                                    operation.isPending ||
                                    !['signed-out', 'error', 'unknown'].includes(backend.state)
                                }
                                onClick={() => {
                                    void start(Operation.START_AUTHENTICATION, backend.id).catch(() => undefined);
                                }}
                                children="Sign in"
                            />
                            <Button
                                variant="default"
                                disabled={data.running || operation.isPending || backend.state !== 'signing-in'}
                                onClick={() => {
                                    void start(Operation.CANCEL_AUTHENTICATION, backend.id).catch(() => undefined);
                                }}
                                children="Cancel sign-in"
                            />
                            <Button
                                variant="default"
                                disabled={data.running || operation.isPending || backend.state !== 'signed-in'}
                                onClick={() => {
                                    void start(Operation.SIGN_OUT, backend.id).catch(() => undefined);
                                }}
                                children="Sign out"
                            />
                        </Group>
                    </Stack>
                </Paper>
            ))}
            <Paper withBorder p="lg">
                <Stack>
                    <Group justify="space-between">
                        <Title order={2} children="Backup" />
                        <Badge children={data.phase} />
                    </Group>
                    {data.recoveryPending && (
                        <Alert
                            color="yellow"
                            title="Recovery required"
                            children="An interrupted backup may have left containers stopped. Recover them before continuing."
                        />
                    )}
                    {data.error && <Failure error={failureError(data.error, data.errorCode)} />}
                    <Text>
                        Last successful backup:{' '}
                        {data.lastSuccess ? new Date(data.lastSuccess).toLocaleString() : 'None yet'}
                    </Text>
                    {backends
                        .filter(backend => backend.transferPhase)
                        .map(backend => (
                            <Stack key={backend.id} gap="xs">
                                <Text>
                                    {backend.name}: {backend.transferPhase} <Code children={backend.transferFile} /> ·{' '}
                                    {backend.transferElapsedSeconds.toString()} s
                                    {backend.transferPercent !== undefined && ` · ${backend.transferPercent}%`}
                                </Text>
                                {backend.transferPercent !== undefined && (
                                    <Progress
                                        value={backend.transferPercent}
                                        aria-label={`${backend.name} transfer progress`}
                                    />
                                )}
                            </Stack>
                        ))}
                    <Group>
                        <Button
                            disabled={
                                data.running ||
                                data.recoveryPending ||
                                data.pendingConfiguration ||
                                operation.isPending ||
                                !targetsReady
                            }
                            onClick={() => {
                                void start(Operation.BACKUP).catch(() => undefined);
                            }}
                            children="Run backup"
                        />
                        <Button
                            variant="default"
                            disabled={!data.running}
                            onClick={() => {
                                void start(Operation.CANCEL_BACKUP).catch(() => undefined);
                            }}
                            children="Cancel backup"
                        />
                        {data.recoveryPending && (
                            <Button
                                color="yellow"
                                onClick={() => {
                                    void start(Operation.RECOVER_CONTAINERS).catch(() => undefined);
                                }}
                                children="Recover containers"
                            />
                        )}
                    </Group>
                </Stack>
            </Paper>
        </Stack>
    );
}
