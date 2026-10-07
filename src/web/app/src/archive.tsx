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
            <Title order={1}>Extract files</Title>
            <Button
                variant="default"
                loading={release.isPending}
                onClick={() => release.mutate({ inspectionId: id ?? '' })}
            >
                Release local copy
            </Button>
            {archive.data && (
                <Alert color="blue" title="Verified archive">
                    The downloaded archive matches its completion manifest. Extraction creates a new directory and
                    preserves the archived paths beneath it. It never replaces the running system or existing files.
                </Alert>
            )}
            <Failure error={archive.error ?? preview.error ?? extraction.error ?? release.error} />
            {!archive.data && !archive.error && <Loading />}
            <Text>
                Directories include their contents. Required parent directories and hard-link targets are included.
            </Text>
            {archive.data?.members.map(entry => (
                <Paper withBorder p="sm" key={entry.path}>
                    <Checkbox
                        label={<Code>{entry.path}</Code>}
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
                            Target: <Code>{entry.linkTarget}</Code>
                        </Text>
                    )}
                    {entry.issue && <Text c="yellow">{entry.issue}</Text>}
                </Paper>
            ))}
            <Group>
                <Button variant="default" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 100))}>
                    Previous entries
                </Button>
                <Text>
                    {archive.data?.total ?? 0} entries · {paths.length} selected
                </Text>
                <Button
                    variant="default"
                    disabled={!archive.data?.nextOffset}
                    onClick={() => setOffset(archive.data?.nextOffset ?? 0)}
                >
                    Next entries
                </Button>
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
                    >
                        Preview extraction
                    </Button>
                </Stack>
            </form>
            {selection && preview.data && (
                <Paper withBorder p="md">
                    <Stack>
                        <Title order={2}>Extraction preview</Title>
                        <Code>{preview.data.destination}</Code>
                        <Text>
                            {preview.data.entries} entries · {preview.data.requiredBytes.toString()} bytes required
                        </Text>
                        {preview.data.includedDependencies.length > 0 && (
                            <>
                                <Text>Also included to preserve directories and hard links:</Text>
                                {preview.data.includedDependencies.map(path => (
                                    <Code key={path}>{path}</Code>
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
                        >
                            Extract selected files
                        </Button>
                    </Stack>
                </Paper>
            )}
        </Stack>
    );
}
