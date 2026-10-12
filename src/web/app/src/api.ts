import { createValidator } from '@bufbuild/protovalidate';
import { Code, ConnectError, createClient } from '@connectrpc/connect';
import { createConnectTransport } from '@connectrpc/connect-web';
import { QueryClient } from '@tanstack/react-query';

import { ControlService } from './generated/protondrive_api/v1/control_pb.ts';

const validator = createValidator();

export const transport = createConnectTransport({
    baseUrl: '/protondrive/rpc',
    interceptors: [
        next => async request => {
            if (!request.stream) {
                const result = validator.validate(request.method.input, request.message);
                if (result.kind !== 'valid') throw new ConnectError(result.error.message, Code.InvalidArgument);
            }
            request.header.set('X-Protondrive-Request', '1');
            if (!['GetStatus', 'GetJob', 'WatchJob'].includes(request.method.name)) {
                request.header.set('X-Protondrive-Activity', '1');
            }
            return next(request);
        },
    ],
    fetch: (input, init) => fetch(input, { ...init, credentials: 'same-origin' }),
});
export const client = createClient(ControlService, transport);
export const queryClient = new QueryClient({
    defaultOptions: {
        queries: { staleTime: 2000, retry: false },
        mutations: { retry: false },
    },
});
