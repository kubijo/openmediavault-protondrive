import { useMutation, useQuery } from '@connectrpc/connect-query';
import { Alert, Button, Checkbox, Code, Group, Paper, Stack, Text, TextInput, Title } from '@mantine/core';
import { useForm } from '@tanstack/react-form';
import type { ReactElement } from 'react';
import { useRef, useState } from 'react';
import { useNavigate, useParams } from 'react-router';

import { Failure, Loading } from './components.tsx';
import { ControlService, Operation } from './generated/protondrive_api/v1/control_pb.ts';

type ExtractionSelection = { inspectionId: string; paths: string[]; destination: string };

export function Archive(): ReactElement {
    const { id } = useParams();
    const navigate = useNavigate();
    const [offset, setOffset] = useState(0);
    const [paths, setPaths] = useState<string[]>([]);
    const archive = useQuery(ControlService.method.getArchive, { inspectionId: id ?? '', offset });
    const extraction = useMutation(ControlService.method.startOperation);
    const release = useMutation(ControlService.method.releaseArchive, {
        onSuccess: () => {
            void navigate('/backups');
        },
    });
    const preview = useMutation(ControlService.method.previewExtraction);
    const [selection, setSelection] = useState<ExtractionSelection>();
    const revision = useRef(0);
    const invalidate = (): void => {
        revision.current++;
        setSelection(undefined);
    };
    const form = useForm({
        defaultValues: { destination: '' },
        onSubmit: async ({ value }) => {
            const planned = { inspectionId: id ?? '', paths, destination: value.destination };
            const requestedRevision = revision.current;
            setSelection(undefined);
            await preview.mutateAsync({ extraction: planned });
            if (revision.current === requestedRevision) setSelection(planned);
        },
    });
    return (
        <Stack>
            <Title order={1} children="Extract files" />
            <Button
                variant="default"
                loading={release.isPending}
                onClick={() => release.mutate({ inspectionId: id ?? '' })}
                children="Release local copy"
            />
            {archive.data && (
                <Alert
                    color="blue"
                    title="Verified archive"
                    children="The downloaded archive matches its completion manifest. Extraction creates a new directory and preserves the archived paths beneath it. It never replaces the running system or existing files."
                />
            )}
            <Failure error={archive.error ?? preview.error ?? extraction.error ?? release.error} />
            {!archive.data && !archive.error && <Loading />}
            {archive.data?.compose.map(project => (
                <Paper withBorder p="md" key={project.project}>
                    <Stack gap="xs">
                        <Title order={2}>Compose project: {project.project}</Title>
                        <Text size="sm">Definitions: {project.definitions.join(', ')}</Text>
                        {project.envFiles.length > 0 && (
                            <Text size="sm">Environment files: {project.envFiles.join(', ')}</Text>
                        )}
                        {project.secretFiles.length > 0 && (
                            <Text size="sm">Secret files: {project.secretFiles.join(', ')}</Text>
                        )}
                        {project.services.map(service => (
                            <Paper withBorder p="sm" key={service.service}>
                                <Text fw={600}>
                                    {service.service} · {service.replicas} replica(s)
                                </Text>
                                <Text size="sm">
                                    Pinned image: <Code children={service.pinned} />
                                </Text>
                                {service.binds.map(bind => (
                                    <Text size="sm" key={`${bind.source}:${bind.target}`}>
                                        Bind: <Code children={bind.source} /> → <Code children={bind.target} />
                                        {bind.readOnly ? ' (read only)' : ''}
                                    </Text>
                                ))}
                            </Paper>
                        ))}
                    </Stack>
                </Paper>
            ))}
            <Text children="Directories include their contents. Required parent directories and hard-link targets are included." />
            {archive.data?.members.map(entry => (
                <Paper withBorder p="sm" key={entry.path}>
                    <Checkbox
                        label={<Code children={entry.path} />}
                        checked={paths.includes(entry.path)}
                        disabled={entry.issue !== ''}
                        onChange={event => {
                            invalidate();
                            const checked = event.currentTarget.checked;
                            setPaths(previous =>
                                checked ? [...previous, entry.path] : previous.filter(path => path !== entry.path),
                            );
                        }}
                    />
                    <Text size="sm">
                        {entry.kind} · {entry.size.toString()} bytes
                    </Text>
                    {entry.linkTarget && (
                        <Text size="sm">
                            Target: <Code children={entry.linkTarget} />
                        </Text>
                    )}
                    {entry.issue && <Text c="yellow" children={entry.issue} />}
                </Paper>
            ))}
            <Group>
                <Button
                    variant="default"
                    disabled={offset === 0}
                    onClick={() => setOffset(Math.max(0, offset - 100))}
                    children="Previous entries"
                />
                <Text>
                    {archive.data?.total ?? 0} entries · {paths.length} selected
                </Text>
                <Button
                    variant="default"
                    disabled={!archive.data?.nextOffset}
                    onClick={() => setOffset(archive.data?.nextOffset ?? 0)}
                    children="Next entries"
                />
            </Group>
            <form
                onSubmit={event => {
                    event.preventDefault();
                    void form.handleSubmit().catch(() => undefined);
                }}
            >
                <Stack>
                    <form.Field name="destination">
                        {field => (
                            <TextInput
                                label="New extraction directory"
                                placeholder="/srv/storage/recovered-files"
                                value={field.state.value}
                                onChange={event => {
                                    invalidate();
                                    field.handleChange(event.currentTarget.value);
                                }}
                                description="Its parent must exist and be administrator-owned. Existing destinations are refused."
                                required
                            />
                        )}
                    </form.Field>
                    <Button
                        type="submit"
                        disabled={!paths.length || paths.length > 1000 || !archive.data}
                        loading={preview.isPending}
                        children="Preview extraction"
                    />
                </Stack>
            </form>
            {selection && preview.data && (
                <Paper withBorder p="md">
                    <Stack>
                        <Title order={2} children="Extraction preview" />
                        <Code children={preview.data.destination} />
                        <Text>
                            {preview.data.entries} entries · {preview.data.requiredBytes.toString()} bytes required
                        </Text>
                        {preview.data.includedDependencies.length > 0 && (
                            <>
                                <Text children="Also included to preserve directories and hard links:" />
                                {preview.data.includedDependencies.map(path => (
                                    <Code key={path} children={path} />
                                ))}
                            </>
                        )}
                        <Button
                            loading={extraction.isPending}
                            onClick={() => {
                                extraction.mutate(
                                    {
                                        operation: Operation.EXTRACT_FILES,
                                        requestId: crypto.randomUUID(),
                                        parameters: { case: 'extractFiles', value: selection },
                                    },
                                    {
                                        onSuccess: response => {
                                            if (response.job) void navigate(`/jobs/${response.job.id}`);
                                        },
                                    },
                                );
                            }}
                            children="Extract selected files"
                        />
                    </Stack>
                </Paper>
            )}
        </Stack>
    );
}
