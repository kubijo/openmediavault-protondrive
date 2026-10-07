import { useMutation, useQuery } from '@connectrpc/connect-query';
import { Button, Checkbox, NumberInput, Stack, TextInput, Title } from '@mantine/core';
import { useForm } from '@tanstack/react-form';
import type { ReactElement } from 'react';
import { useState } from 'react';

import { queryClient } from './api.ts';
import { Failure, Loading } from './components.tsx';
import type { Configuration } from './generated/protondrive_api/v1/control_pb.ts';
import { ControlService } from './generated/protondrive_api/v1/control_pb.ts';

export function Settings(): ReactElement {
    const query = useQuery(ControlService.method.getConfiguration);
    const data = query.data;
    if (!data?.configuration) return query.error ? <Failure error={query.error} /> : <Loading />;
    return <SettingsEditor key={data.revision} configuration={data.configuration} revision={data.revision} />;
}

export function SettingsEditor({
    configuration,
    revision,
}: {
    configuration: Configuration;
    revision: string;
}): ReactElement {
    const save = useMutation(ControlService.method.saveConfiguration);
    const [error, setError] = useState<Error | null>(null);
    const form = useForm({
        defaultValues: { ...configuration, minimumFreeBytes: configuration.minimumFreeBytes.toString() },
        onSubmit: async ({ value }) => {
            try {
                await save.mutateAsync({
                    configuration: { ...value, minimumFreeBytes: BigInt(value.minimumFreeBytes) },
                    revision,
                });
                await queryClient.invalidateQueries();
            } catch (reason) {
                setError(reason instanceof Error ? reason : new Error('Could not save settings'));
            }
        },
    });
    const numeric = [
        ['scheduleHour', 'Backup hour (server time)', 0, 23],
        ['scheduleMinute', 'Backup minute', 0, 59],
        ['containerStopTimeoutSeconds', 'Container stop timeout (seconds)', 1, 600],
        ['commandTimeoutSeconds', 'Command timeout (seconds)', 1, 600],
        ['transferTimeoutSeconds', 'Transfer timeout (seconds)', 60, 86400],
    ] as const;
    const text = [
        ['stagingPath', 'Local staging directory'],
        ['remotePath', 'Proton Drive root directory'],
        ['minimumFreeBytes', 'Minimum free space (bytes)'],
    ] as const;
    return (
        <form
            onSubmit={event => {
                event.preventDefault();
                void form.handleSubmit();
            }}
        >
            <Stack>
                <Title order={1}>Settings</Title>
                <Failure error={error} />
                <form.Field name="enabled">
                    {field => (
                        <Checkbox
                            label="Enable scheduled backups"
                            checked={field.state.value}
                            onChange={event => field.handleChange(event.currentTarget.checked)}
                        />
                    )}
                </form.Field>
                {numeric.map(([name, label, min, max]) => (
                    <form.Field key={name} name={name}>
                        {field => (
                            <NumberInput
                                label={label}
                                min={min}
                                max={max}
                                allowDecimal={false}
                                value={field.state.value}
                                onBlur={field.handleBlur}
                                onChange={value => {
                                    if (typeof value === 'number') field.handleChange(value);
                                }}
                            />
                        )}
                    </form.Field>
                ))}
                {text.map(([name, label]) => (
                    <form.Field key={name} name={name}>
                        {field => (
                            <TextInput
                                required
                                label={label}
                                value={field.state.value}
                                onBlur={field.handleBlur}
                                onChange={event => field.handleChange(event.currentTarget.value)}
                            />
                        )}
                    </form.Field>
                ))}
                <Button type="submit" loading={save.isPending}>
                    Save settings
                </Button>
            </Stack>
        </form>
    );
}
