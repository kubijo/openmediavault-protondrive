import { useMutation } from '@connectrpc/connect-query';
import { Alert, Badge, Button, Group, Stack, Text, Title } from '@mantine/core';
import type { ReactElement } from 'react';
import { useEffect, useState } from 'react';
import { Link, useParams } from 'react-router';

import { client } from './api.ts';
import { Failure, failureError } from './components.tsx';
import type { Job } from './generated/protondrive_api/v1/control_pb.ts';
import { ControlService, JobState, Operation } from './generated/protondrive_api/v1/control_pb.ts';

export function finished(state: number): boolean {
    return (
        state === JobState.SUCCEEDED ||
        state === JobState.FAILED ||
        state === JobState.INTERRUPTED ||
        state === JobState.CANCELLED
    );
}

export function JobView(): ReactElement {
    const { id } = useParams();
    const [job, setJob] = useState<Job>();
    const [error, setError] = useState<Error | null>(null);
    const cancellation = useMutation(ControlService.method.cancelJob);
    useEffect(() => {
        if (!id) return;
        const controller = new AbortController();
        const follow = async (): Promise<void> => {
            let cursor = 0n;
            while (!controller.signal.aborted) {
                for await (const update of client.watchJob(
                    { id, afterSequence: cursor },
                    { signal: controller.signal },
                )) {
                    if (!update.job) throw new Error('The controller returned an incomplete job event');
                    setJob(update.job);
                    cursor = update.job.sequence;
                    if (finished(update.job.state)) return;
                }
            }
        };
        void follow().catch((reason: unknown) => {
            if (!controller.signal.aborted) setError(reason instanceof Error ? reason : new Error('Job stream failed'));
        });
        return () => controller.abort();
    }, [id]);
    return (
        <Stack>
            <Group justify="space-between">
                <Title order={1} children="Operation" />
                <Badge
                    children={
                        job
                            ? (Object.entries(JobState).find(([, value]) => value === job.state)?.[0] ?? 'Unknown')
                            : 'Connecting'
                    }
                />
            </Group>
            <Failure error={error ?? cancellation.error} />
            {job && (job.state === JobState.FAILED || (job.state === JobState.INTERRUPTED && job.failureCode)) ? (
                <Failure error={failureError(job.message, job.failureCode)} />
            ) : (
                <Text children={job?.message} />
            )}
            {job &&
                !finished(job.state) &&
                (job.operation === Operation.INSPECT_ARCHIVE || job.operation === Operation.EXTRACT_FILES) && (
                    <Button
                        color="red"
                        loading={cancellation.isPending}
                        onClick={() => cancellation.mutate({ id: job.id })}
                        children="Cancel operation"
                    />
                )}
            {job?.state === JobState.SUCCEEDED && job.operation === Operation.INSPECT_ARCHIVE && (
                <Button component={Link} to={`/archives/${job.id}`} children="Browse verified archive" />
            )}
            {job?.state === JobState.INTERRUPTED && (
                <Alert
                    color="yellow"
                    children="Inspect backup status before retrying. The underlying operation may still be running."
                />
            )}
            <Button component={Link} to="/" variant="default" children="Back to overview" />
        </Stack>
    );
}
