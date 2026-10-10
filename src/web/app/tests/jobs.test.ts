import { create } from '@bufbuild/protobuf';
import { createValidator } from '@bufbuild/protovalidate';
import { describe, expect, it } from '@rstest/core';
import { JobState, Operation, StartOperationRequestSchema } from '../src/generated/protondrive_api/v1/control_pb.ts';
import { finished } from '../src/job.tsx';

describe('job protocol', () => {
    it('does not treat a queued or unknown state as completion', () => {
        expect(finished(JobState.QUEUED)).toBe(false);
        expect(finished(JobState.RUNNING)).toBe(false);
        expect(finished(99)).toBe(false);
        expect(finished(JobState.SUCCEEDED)).toBe(true);
        expect(finished(JobState.INTERRUPTED)).toBe(true);
        expect(finished(JobState.CANCELLED)).toBe(true);
    });

    it('enforces the same request identifier constraint in the browser', () => {
        const validator = createValidator();
        const invalid = create(StartOperationRequestSchema, { operation: Operation.BACKUP, requestId: 'invalid' });
        expect(validator.validate(StartOperationRequestSchema, invalid).kind).toBe('invalid');
        const valid = create(StartOperationRequestSchema, {
            operation: Operation.BACKUP,
            requestId: crypto.randomUUID(),
        });
        expect(validator.validate(StartOperationRequestSchema, valid).kind).toBe('valid');
    });
});
