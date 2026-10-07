import { createRequire } from 'node:module';
import { defineConfig } from '@rsbuild/core';
import { pluginReact } from '@rsbuild/plugin-react';
import { pluginSass } from '@rsbuild/plugin-sass';

const require = createRequire(import.meta.url);

export default defineConfig({
    plugins: [
        pluginReact({ reactCompiler: true }),
        pluginSass({ sassLoaderOptions: { implementation: require.resolve('sass') } }),
    ],
    source: { entry: { index: './src/main.tsx' } },
    html: { title: 'Proton Drive · OpenMediaVault' },
    output: { assetPrefix: '/protondrive/', target: 'web' },
    server: { base: '/protondrive/' },
});
