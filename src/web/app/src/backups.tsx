import { useMutation, useQuery } from '@connectrpc/connect-query';
import { Alert, Button, Code, Group, Paper, Select, Stack, Text, Title } from '@mantine/core';
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
    const config = useQuery(ControlService.method.getConfiguration);
    const destinationId =
        parameters.get('destination') ?? config.data?.configuration?.destinations.find(item => item.enabled)?.id ?? '';
    const result = useQuery(ControlService.method.browseBackups, { instanceId, setId, destinationId });
    const sets = useQuery(ControlService.method.listSets);
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
                <Title order={1} children="Remote backups" />
                <Button
                    variant="default"
                    onClick={() => {
                        void result.refetch();
                    }}
                    children="Refresh"
                />
            </Group>
            <Select
                label="Destination"
                data={
                    config.data?.configuration?.destinations.map(item => ({ value: item.id, label: item.name })) ?? []
                }
                value={destinationId || null}
                onChange={id => {
                    if (id) void setParameters({ destination: id });
                }}
            />
            {instanceId && (
                <Button
                    variant="default"
                    onClick={() => {
                        void setParameters(
                            setId
                                ? { destination: destinationId, instance: instanceId }
                                : { destination: destinationId },
                        );
                    }}
                    children="Parent directory"
                />
            )}
            {data?.foreignInstance && (
                <Alert
                    color="blue"
                    title="Another instance"
                    children="Browsing is read-only. This NAS will not claim these backups or apply retention."
                />
            )}
            <Text children="Archive and manifest pairs are listed here. Their contents and checksums have not yet been verified." />
            <Failure error={result.error} />
            <Failure error={inspection.error} />
            <Failure error={cached.error ?? release.error} />
            {(cached.data?.archives.length ?? 0) > 0 && <Title order={2} children="Downloaded archives" />}
            {cached.data?.archives.map(item => (
                <Paper
                    component="section"
                    aria-label={`Cached archive ${item.inspectionId}`}
                    withBorder
                    p="sm"
                    key={item.inspectionId}
                >
                    <Stack>
                        <Code children={item.source?.name} />
                        <Group>
                            <Button
                                component={Link}
                                to={item.ready ? `/archives/${item.inspectionId}` : `/jobs/${item.inspectionId}`}
                                children={item.ready ? 'Browse inspected archive' : 'View inspection job'}
                            />
                            <Button
                                variant="default"
                                loading={release.isPending}
                                onClick={() => release.mutate({ inspectionId: item.inspectionId })}
                                children="Release local copy"
                            />
                        </Group>
                    </Stack>
                </Paper>
            ))}
            {!data && !result.error && <Loading />}
            {data?.entries.length === 0 && <Text children="No entries in this directory." />}
            {data?.entries.map(item => (
                <Paper key={item.name} withBorder p="md">
                    {item.directory &&
                    !setId &&
                    /^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/.test(item.name) ? (
                        <Button
                            variant="default"
                            onClick={() => {
                                void setParameters(
                                    instanceId
                                        ? { destination: destinationId, instance: instanceId, set: item.name }
                                        : { destination: destinationId, instance: item.name },
                                );
                            }}
                            children={name(item.name)}
                        />
                    ) : (
                        <>
                            <Code children={item.name} />
                            <Text
                                children={
                                    item.completePair
                                        ? 'Archive and manifest present'
                                        : 'Metadata, incomplete pair, or unsupported entry'
                                }
                            />
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
                                                    value: { instanceId, setId, name: item.name, destinationId },
                                                },
                                            },
                                            {
                                                onSuccess: response => {
                                                    if (response.job) void navigate(`/jobs/${response.job.id}`);
                                                },
                                            },
                                        );
                                    }}
                                    children="Download and inspect"
                                />
                            )}
                        </>
                    )}
                    {item.size !== undefined && <Text size="sm">{item.size.toString()} bytes</Text>}
                </Paper>
            ))}
        </Stack>
    );
}
