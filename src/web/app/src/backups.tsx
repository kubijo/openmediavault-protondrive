import { useMutation, useQuery } from '@connectrpc/connect-query';
import { Alert, Button, Code, Group, Paper, Stack, Text, Title } from '@mantine/core';
import type { ReactElement } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router';
import { Failure, Loading } from './components.tsx';
import { ControlService, Operation } from './generated/protondrive_api/v1/control_pb.ts';

export function Backups(): ReactElement {
    const navigate = useNavigate();
    const inspection = useMutation(ControlService.method.startOperation);
    const cached = useQuery(ControlService.method.listArchives);
    const release = useMutation(ControlService.method.releaseArchive, {
        onSuccess: () => {
            void cached.refetch();
        },
    });
    const [parameters, setParameters] = useSearchParams();
    const instanceId = parameters.get('instance') ?? undefined;
    const setId = parameters.get('set') ?? undefined;
    const result = useQuery(ControlService.method.browseBackups, { instanceId, setId });
    const sets = useQuery(ControlService.method.listSets);
    const config = useQuery(ControlService.method.getConfiguration);
    const data = result.data;
    const name = (id: string): string =>
        instanceId
            ? (sets.data?.sets.find(item => item.id === id)?.name ?? id)
            : config.data?.configuration?.instanceId === id
              ? 'This NAS'
              : id;
    return (
        <Stack>
            <Group justify="space-between">
                <Title order={1}>Remote backups</Title>
                <Button
                    variant="default"
                    onClick={() => {
                        void result.refetch();
                    }}
                >
                    Refresh
                </Button>
            </Group>
            {instanceId && (
                <Button
                    variant="default"
                    onClick={() => {
                        void setParameters(setId ? { instance: instanceId } : {});
                    }}
                >
                    Parent directory
                </Button>
            )}
            {data?.foreignInstance && (
                <Alert color="blue" title="Another instance">
                    Browsing is read-only. This NAS will not claim these backups or apply retention.
                </Alert>
            )}
            <Text>
                Archive and manifest pairs are listed here. Their contents and checksums have not yet been verified.
            </Text>
            <Failure error={result.error} />
            <Failure error={inspection.error} />
            <Failure error={cached.error ?? release.error} />
            {(cached.data?.archives.length ?? 0) > 0 && <Title order={2}>Downloaded archives</Title>}
            {cached.data?.archives.map(item => (
                <Paper
                    component="section"
                    aria-label={`Cached archive ${item.inspectionId}`}
                    withBorder
                    p="sm"
                    key={item.inspectionId}
                >
                    <Stack>
                        <Code>{item.source?.name}</Code>
                        <Group>
                            <Button
                                component={Link}
                                to={item.ready ? `/archives/${item.inspectionId}` : `/jobs/${item.inspectionId}`}
                            >
                                {item.ready ? 'Browse inspected archive' : 'View inspection job'}
                            </Button>
                            <Button
                                variant="default"
                                loading={release.isPending}
                                onClick={() => release.mutate({ inspectionId: item.inspectionId })}
                            >
                                Release local copy
                            </Button>
                        </Group>
                    </Stack>
                </Paper>
            ))}
            {!data && !result.error && <Loading />}
            {data?.entries.length === 0 && <Text>No entries in this directory.</Text>}
            {data?.entries.map(item => (
                <Paper key={item.name} withBorder p="md">
                    {item.directory &&
                    !setId &&
                    /^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/.test(item.name) ? (
                        <Button
                            variant="default"
                            onClick={() => {
                                void setParameters(
                                    instanceId ? { instance: instanceId, set: item.name } : { instance: item.name },
                                );
                            }}
                        >
                            {name(item.name)}
                        </Button>
                    ) : (
                        <>
                            <Code>{item.name}</Code>
                            <Text>
                                {item.completePair
                                    ? 'Archive and manifest present'
                                    : 'Metadata, incomplete pair, or unsupported entry'}
                            </Text>
                            {item.completePair && instanceId && setId && (
                                <Button
                                    mt="sm"
                                    loading={inspection.isPending}
                                    onClick={() => {
                                        inspection.mutate(
                                            {
                                                operation: Operation.INSPECT_ARCHIVE,
                                                requestId: crypto.randomUUID(),
                                                parameters: {
                                                    case: 'inspectArchive',
                                                    value: { instanceId, setId, name: item.name },
                                                },
                                            },
                                            {
                                                onSuccess: response => {
                                                    if (response.job) void navigate(`/jobs/${response.job.id}`);
                                                },
                                            },
                                        );
                                    }}
                                >
                                    Download and inspect
                                </Button>
                            )}
                        </>
                    )}
                    {item.size !== undefined && <Text size="sm">{item.size.toString()} bytes</Text>}
                </Paper>
            ))}
        </Stack>
    );
}
