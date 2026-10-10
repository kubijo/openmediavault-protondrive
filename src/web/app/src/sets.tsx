import { create } from '@bufbuild/protobuf';
import { useMutation, useQuery } from '@connectrpc/connect-query';
import {
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
import type { BackupSet } from './generated/protondrive_api/v1/control_pb.ts';
import {
    BackupSetSchema,
    ComposeApplicationSchema,
    ControlService,
} from './generated/protondrive_api/v1/control_pb.ts';

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
                        <Text children={item.paths.join(', ')} />
                        <Text
                            children={
                                item.stopAllContainers
                                    ? 'Stops all running containers (existing policy)'
                                    : item.containerIds.length || item.composeProjects.length
                                      ? `Stops ${item.containerIds.length} selected containers and ${item.composeProjects.length} Compose projects`
                                      : 'Does not stop containers'
                            }
                        />
                        {item.composeApplications.length > 0 && (
                            <Text>
                                {item.composeApplications.length} Compose applications captured for reconstruction
                            </Text>
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
    const inventory = useQuery(ControlService.method.listContainers);
    const containers = inventory.data?.containers ?? [];
    const [composeApplications, setComposeApplications] = useState(value.composeApplications);
    const form = useForm({
        defaultValues: { ...value, paths: value.paths.join('\n'), exclusions: value.exclusions.join('\n') },
        onSubmit: async ({ value: edited }) => {
            try {
                await save.mutateAsync({
                    set: {
                        ...edited,
                        paths: edited.paths.split('\n').filter(Boolean),
                        exclusions: edited.exclusions.split('\n').filter(Boolean),
                        composeApplications: composeApplications.map(app => ({
                            ...app,
                            definitions: app.definitions.filter(Boolean),
                            envFiles: app.envFiles.filter(Boolean),
                            secretFiles: app.secretFiles.filter(Boolean),
                        })),
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
                <form.Field name="containerIds">
                    {field => (
                        <MultiSelect
                            label="Containers to stop while archiving"
                            searchable
                            clearable
                            description="Only previously running containers will be restarted."
                            data={[
                                ...containers.map(item => ({ value: item.id, label: item.name })),
                                ...field.state.value
                                    .filter(id => !containers.some(item => item.id === id))
                                    .map(id => ({ value: id, label: `Missing container: ${id}` })),
                            ]}
                            value={field.state.value}
                            onChange={next => {
                                field.handleChange(next);
                                if (next.length) form.setFieldValue('stopAllContainers', false);
                            }}
                        />
                    )}
                </form.Field>
                <form.Field name="composeProjects">
                    {field => (
                        <MultiSelect
                            label="Compose projects to stop while archiving"
                            searchable
                            clearable
                            data={[
                                ...new Set([
                                    ...containers.map(item => item.composeProject).filter(Boolean),
                                    ...field.state.value,
                                ]),
                            ]}
                            value={field.state.value}
                            onChange={next => {
                                field.handleChange(next);
                                setComposeApplications(current => current.filter(app => next.includes(app.project)));
                                if (next.length) form.setFieldValue('stopAllContainers', false);
                            }}
                        />
                    )}
                </form.Field>
                <form.Field name="composeProjects">
                    {field => (
                        <Stack gap="xs">
                            <Title order={2} children="Recoverable Compose applications" />
                            <Text
                                size="sm"
                                children="Select each application’s Compose files explicitly. Every file and bind source must be covered by the backup sources. Images need pullable registry digests; Docker volumes are unsupported."
                            />
                            {composeApplications.map((app, index) => (
                                <Paper key={app.project} withBorder p="md">
                                    <Stack>
                                        <Group justify="space-between">
                                            <Text fw={600} children={app.project} />
                                            <Button
                                                type="button"
                                                color="red"
                                                variant="outline"
                                                onClick={() =>
                                                    setComposeApplications(current =>
                                                        current.filter((_, i) => i !== index),
                                                    )
                                                }
                                                children="Remove"
                                            />
                                        </Group>
                                        {(['definitions', 'envFiles', 'secretFiles'] as const).map(name => (
                                            <Textarea
                                                key={name}
                                                label={
                                                    name === 'definitions'
                                                        ? 'Compose definition files, in order'
                                                        : name === 'envFiles'
                                                          ? 'Environment files'
                                                          : 'Secret files'
                                                }
                                                description="Absolute paths, one per line"
                                                required={name === 'definitions'}
                                                minRows={2}
                                                value={app[name].join('\n')}
                                                onChange={event => {
                                                    const files = event.currentTarget.value.split('\n');
                                                    setComposeApplications(current =>
                                                        current.map((entry, i) =>
                                                            i === index ? { ...entry, [name]: files } : entry,
                                                        ),
                                                    );
                                                }}
                                            />
                                        ))}
                                    </Stack>
                                </Paper>
                            ))}
                            <Select
                                label="Add application for a selected project"
                                placeholder="Choose project"
                                clearable
                                data={field.state.value.filter(
                                    project => !composeApplications.some(app => app.project === project),
                                )}
                                value={null}
                                onChange={project => {
                                    if (project)
                                        setComposeApplications(current => [
                                            ...current,
                                            create(ComposeApplicationSchema, {
                                                project,
                                                definitions: [],
                                                envFiles: [],
                                                secretFiles: [],
                                            }),
                                        ]);
                                }}
                            />
                        </Stack>
                    )}
                </form.Field>
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
                            required
                            minRows={3}
                            value={field.state.value}
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
                <form.Field name="stopAllContainers">
                    {field => (
                        <Checkbox
                            label="Stop all running containers during this backup"
                            checked={field.state.value}
                            onChange={event => {
                                field.handleChange(event.currentTarget.checked);
                                if (event.currentTarget.checked) {
                                    form.setFieldValue('containerIds', []);
                                    form.setFieldValue('composeProjects', []);
                                    setComposeApplications([]);
                                }
                            }}
                        />
                    )}
                </form.Field>
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
                    <Button type="submit" loading={save.isPending} children="Save backup set" />
                    <Button type="button" variant="default" onClick={close} children="Cancel" />
                </Group>
            </Stack>
        </form>
    );
}
