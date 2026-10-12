import { create } from '@bufbuild/protobuf';
import { useMutation, useQuery } from '@connectrpc/connect-query';
import {
    Alert,
    Button,
    Checkbox,
    Group,
    MultiSelect,
    NumberInput,
    Paper,
    Select,
    Stack,
    Text,
    Textarea,
    TextInput,
    Title,
} from '@mantine/core';
import { useForm } from '@tanstack/react-form';
import type { ReactElement } from 'react';
import { useState } from 'react';

import { queryClient } from './api.ts';
import { Failure, Loading } from './components.tsx';
import type { BackupSet, ComposeSource } from './generated/protondrive_api/v1/control_pb.ts';
import { BackupSetSchema, ComposeSourceSchema, ControlService } from './generated/protondrive_api/v1/control_pb.ts';

export function BackupSets(): ReactElement {
    const query = useQuery(ControlService.method.listSets);
    const remove = useMutation(ControlService.method.deleteSet);
    const [editing, setEditing] = useState<{ value: BackupSet; revision: string }>();
    const [confirmDelete, setConfirmDelete] = useState<string>();
    const data = query.data;
    if (!data) return query.error ? <Failure error={query.error} /> : <Loading />;
    if (editing) return <SetEditor {...editing} close={() => setEditing(undefined)} />;
    return (
        <Stack>
            <Group justify="space-between">
                <Title order={1} children="Backup sets" />
                <Button
                    onClick={() =>
                        setEditing({
                            value: create(BackupSetSchema, { enabled: true, localKeep: 2, remoteKeep: 7 }),
                            revision: data.revision,
                        })
                    }
                    children="Add backup set"
                />
            </Group>
            <Failure error={remove.error} />
            {data.sets.map(item => (
                <Paper key={item.id} component="section" aria-label={`Backup set ${item.name}`} withBorder p="md">
                    <Stack>
                        <Title order={2} children={item.name} />
                        {item.migrationRequired && (
                            <Alert
                                color="yellow"
                                title="Review required"
                                children="This set used container controls that this plugin no longer runs. Review its sources before the next backup."
                            />
                        )}
                        <Text children={item.paths.length ? item.paths.join(', ') : 'No ordinary file paths'} />
                        <Text children={`${item.composeSources.length} OMV Compose source(s)`} />
                        {item.composeSources.length > 0 && (
                            <Text
                                c="yellow"
                                children="Compose capture is best effort; archive and upload checks cannot prove its files were copied consistently."
                            />
                        )}
                        <Group>
                            <Button
                                variant="default"
                                onClick={() => setEditing({ value: item, revision: data.revision })}
                                children="Edit"
                            />
                            <Button
                                color="red"
                                variant="outline"
                                onClick={() => setConfirmDelete(item.id)}
                                children="Delete"
                            />
                            {confirmDelete === item.id && (
                                <>
                                    <Text children="Delete this configuration? Existing archives remain." />
                                    <Button
                                        color="red"
                                        onClick={() => {
                                            void remove
                                                .mutateAsync({ id: item.id, revision: data.revision })
                                                .then(async () => {
                                                    setConfirmDelete(undefined);
                                                    await queryClient.invalidateQueries();
                                                })
                                                .catch(() => undefined);
                                        }}
                                        children="Confirm deletion"
                                    />
                                    <Button
                                        variant="default"
                                        onClick={() => setConfirmDelete(undefined)}
                                        children="Keep set"
                                    />
                                </>
                            )}
                        </Group>
                    </Stack>
                </Paper>
            ))}
        </Stack>
    );
}

function SetEditor({
    value,
    revision,
    close,
}: {
    value: BackupSet;
    revision: string;
    close: () => void;
}): ReactElement {
    const save = useMutation(ControlService.method.saveSet);
    const inventory = useQuery(ControlService.method.listComposeSources);
    const [composeSources, setComposeSources] = useState<ComposeSource[]>(value.composeSources);
    const [reviewed, setReviewed] = useState(false);
    const form = useForm({
        defaultValues: { ...value, paths: value.paths.join('\n'), exclusions: value.exclusions.join('\n') },
        onSubmit: async ({ value: edited }) => {
            if (value.migrationRequired && !reviewed) return;
            try {
                await save.mutateAsync({
                    set: {
                        ...edited,
                        paths: edited.paths
                            .split('\n')
                            .map(path => path.trim())
                            .filter(Boolean),
                        exclusions: edited.exclusions
                            .split('\n')
                            .map(path => path.trim())
                            .filter(Boolean),
                        composeSources,
                        stopAllContainers: false,
                        containerIds: [],
                        composeProjects: [],
                        composeApplications: [],
                        migrationRequired: false,
                    },
                    revision,
                });
                await queryClient.invalidateQueries();
                close();
            } catch {
                /* Connect-Query exposes the error below; retain unsaved fields. */
            }
        },
    });
    const availableJobs = inventory.data?.jobs ?? [];
    const availableStacks = inventory.data?.stacks ?? [];
    return (
        <form
            onSubmit={event => {
                event.preventDefault();
                void form.handleSubmit();
            }}
        >
            <Stack>
                <Title order={1} children={value.id ? 'Edit backup set' : 'Add backup set'} />
                <Failure error={save.error} />
                <Failure error={inventory.error} />
                {value.migrationRequired && (
                    <Alert
                        color="yellow"
                        title="Review container backup policy"
                        children="The previous container stop and reconstruction settings will be removed when you save. Choose an OMV Compose source or confirm that backing up live files is acceptable."
                    />
                )}
                <form.Field name="name">
                    {field => (
                        <TextInput
                            label="Name"
                            required
                            disabled={!!value.id}
                            value={field.state.value}
                            onChange={event => field.handleChange(event.currentTarget.value)}
                        />
                    )}
                </form.Field>
                <form.Field name="enabled">
                    {field => (
                        <Checkbox
                            label="Enabled"
                            checked={field.state.value}
                            onChange={event => field.handleChange(event.currentTarget.checked)}
                        />
                    )}
                </form.Field>
                <form.Field name="paths">
                    {field => (
                        <Textarea
                            label="Source directories, one per line"
                            minRows={3}
                            value={field.state.value}
                            description="Live files may change while they are archived. Use an application export or a snapshot for mutable databases."
                            onChange={event => field.handleChange(event.currentTarget.value)}
                        />
                    )}
                </form.Field>
                <form.Field name="exclusions">
                    {field => (
                        <Textarea
                            label="Excluded relative paths, one per line"
                            minRows={3}
                            value={field.state.value}
                            onChange={event => field.handleChange(event.currentTarget.value)}
                        />
                    )}
                </form.Field>
                <Title order={2} children="OMV Compose sources" />
                <Text
                    size="sm"
                    children="Our schedule runs the selected Compose backup jobs, then archives their selected stack folders. Disable these jobs’ schedules in OMV Compose. Capture is best effort because Compose does not reliably report every rsync failure."
                />
                {inventory.data && !inventory.data.available && <Text c="dimmed" children={inventory.data.message} />}
                {inventory.data?.available && availableJobs.length === 0 && (
                    <Text c="dimmed" children="Create an unscheduled backup-only job in OMV Compose to add a source." />
                )}
                {inventory.data?.available && inventory.data.maxVolumeSize > 0n && (
                    <Alert
                        color="yellow"
                        children={`OMV Compose skips volumes above its configured size limit (${inventory.data.maxVolumeSize.toString()} bytes) unless explicitly included.`}
                    />
                )}
                {composeSources.map((source, index) => (
                    <Paper key={index} withBorder p="md">
                        <Stack>
                            <Select
                                label="Compose backup job"
                                data={availableJobs.map(job => ({
                                    value: job.id,
                                    label: job.name,
                                    disabled: job.scheduled,
                                }))}
                                value={source.jobId}
                                onChange={jobId => {
                                    if (jobId)
                                        setComposeSources(current =>
                                            current.map((entry, i) => (i === index ? { ...entry, jobId } : entry)),
                                        );
                                }}
                            />
                            <MultiSelect
                                label="Stack folders to capture"
                                searchable
                                data={[...new Set([...availableStacks, ...source.stacks])]}
                                value={source.stacks}
                                onChange={stacks =>
                                    setComposeSources(current =>
                                        current.map((entry, i) => (i === index ? { ...entry, stacks } : entry)),
                                    )
                                }
                            />
                            <Button
                                type="button"
                                color="red"
                                variant="outline"
                                onClick={() => setComposeSources(current => current.filter((_, i) => i !== index))}
                                children="Remove Compose source"
                            />
                        </Stack>
                    </Paper>
                ))}
                <Button
                    type="button"
                    variant="default"
                    disabled={!availableJobs.some(job => !job.scheduled)}
                    onClick={() => {
                        const job = availableJobs.find(
                            entry => !entry.scheduled && !composeSources.some(source => source.jobId === entry.id),
                        );
                        if (job)
                            setComposeSources(current => [...current, create(ComposeSourceSchema, { jobId: job.id })]);
                    }}
                    children="Add Compose source"
                />
                {value.migrationRequired && (
                    <Checkbox
                        label="I reviewed this set’s sources and understand its old container controls will be removed"
                        checked={reviewed}
                        onChange={event => setReviewed(event.currentTarget.checked)}
                    />
                )}
                {(['localKeep', 'remoteKeep'] as const).map(name => (
                    <form.Field key={name} name={name}>
                        {field => (
                            <NumberInput
                                label={name === 'localKeep' ? 'Local copies to retain' : 'Remote copies to retain'}
                                min={1}
                                max={10000}
                                allowDecimal={false}
                                value={field.state.value}
                                onChange={next => {
                                    if (typeof next === 'number') field.handleChange(next);
                                }}
                            />
                        )}
                    </form.Field>
                ))}
                <Group>
                    <Button
                        type="submit"
                        loading={save.isPending}
                        disabled={value.migrationRequired && !reviewed}
                        children="Save backup set"
                    />
                    <Button type="button" variant="default" onClick={close} children="Cancel" />
                </Group>
            </Stack>
        </form>
    );
}
