import { useMutation, useQuery } from '@connectrpc/connect-query';
import { Alert, Anchor, Badge, Button, Code, Group, Paper, Stack, Text, Title } from '@mantine/core';
import type { ReactElement } from 'react';
import { useNavigate } from 'react-router';

import { Failure, Loading } from './components.tsx';
import { ControlService, Operation } from './generated/protondrive_api/v1/control_pb.ts';

export function Overview(): ReactElement {
    const status = useQuery(ControlService.method.getStatus, {}, { refetchInterval: 3000 });
    const operation = useMutation(ControlService.method.startOperation);
    const navigate = useNavigate();
    const start = async (kind: Operation): Promise<void> => {
        const result = await operation.mutateAsync({ operation: kind, requestId: crypto.randomUUID() });
        if (result.job) await navigate(`/jobs/${result.job.id}`);
    };
    const data = status.data;
    if (!data) return status.error ? <Failure error={status.error} /> : <Loading />;
    return (
        <Stack>
            <Title order={1}>Proton Drive</Title>
            <Failure error={operation.error} />
            {data.pendingConfiguration && (
                <Alert color="yellow" title="Pending configuration">
                    Saved changes need to be applied before the next backup.
                    <Button
                        onClick={() => {
                            void start(Operation.APPLY_CONFIGURATION).catch(() => undefined);
                        }}
                    >
                        Apply changes
                    </Button>
                </Alert>
            )}
            <Paper withBorder p="lg">
                <Stack>
                    <Title order={2}>Account</Title>
                    <Text>{data.accountState === 'signed-in' ? 'Signed in' : data.accountState}</Text>
                    {data.accountError && (
                        <Alert color="yellow" title="Account status unavailable">
                            {data.accountError}
                        </Alert>
                    )}
                    {data.accountEmail && <Text>{data.accountEmail}</Text>}
                    {data.authenticationUrl && (
                        <Anchor href={data.authenticationUrl} target="_blank" rel="noopener noreferrer">
                            Complete Proton sign-in
                        </Anchor>
                    )}
                    <Group>
                        <Button
                            variant="default"
                            disabled={
                                data.running ||
                                operation.isPending ||
                                !['signed-out', 'error', 'unknown'].includes(data.accountState)
                            }
                            onClick={() => {
                                void start(Operation.START_AUTHENTICATION).catch(() => undefined);
                            }}
                        >
                            Sign in
                        </Button>
                        <Button
                            variant="default"
                            disabled={data.running || operation.isPending || data.accountState !== 'signing-in'}
                            onClick={() => {
                                void start(Operation.CANCEL_AUTHENTICATION).catch(() => undefined);
                            }}
                        >
                            Cancel sign-in
                        </Button>
                        <Button
                            variant="default"
                            disabled={data.running || operation.isPending || data.accountState !== 'signed-in'}
                            onClick={() => {
                                void start(Operation.SIGN_OUT).catch(() => undefined);
                            }}
                        >
                            Sign out
                        </Button>
                    </Group>
                </Stack>
            </Paper>
            <Paper withBorder p="lg">
                <Stack>
                    <Group justify="space-between">
                        <Title order={2}>Backup</Title>
                        <Badge>{data.phase}</Badge>
                    </Group>
                    {data.recoveryPending && (
                        <Alert color="yellow" title="Recovery required">
                            An interrupted backup may have left containers stopped. Recover them before continuing.
                        </Alert>
                    )}
                    {data.error && <Alert color="red">{data.error}</Alert>}
                    <Text>
                        Last successful backup:{' '}
                        {data.lastSuccess ? new Date(data.lastSuccess).toLocaleString() : 'None yet'}
                    </Text>
                    {data.transferPhase && (
                        <Text>
                            {data.transferPhase}: <Code>{data.transferFile}</Code> ·{' '}
                            {data.transferElapsedSeconds.toString()} s
                        </Text>
                    )}
                    <Group>
                        <Button
                            disabled={
                                data.running ||
                                data.recoveryPending ||
                                data.pendingConfiguration ||
                                operation.isPending ||
                                data.accountState !== 'signed-in'
                            }
                            onClick={() => {
                                void start(Operation.BACKUP).catch(() => undefined);
                            }}
                        >
                            Run backup
                        </Button>
                        <Button
                            variant="default"
                            disabled={!data.running}
                            onClick={() => {
                                void start(Operation.CANCEL_BACKUP).catch(() => undefined);
                            }}
                        >
                            Cancel backup
                        </Button>
                        {data.recoveryPending && (
                            <Button
                                color="yellow"
                                onClick={() => {
                                    void start(Operation.RECOVER_CONTAINERS).catch(() => undefined);
                                }}
                            >
                                Recover containers
                            </Button>
                        )}
                    </Group>
                </Stack>
            </Paper>
        </Stack>
    );
}
