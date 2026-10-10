set default-list

# Build the installable Debian package.
mod app 'tools/just/app.just'

# Run development checks, audits, and update reports.
mod dev 'tools/just/dev.just'

# Run unit tests and disposable guest integration tests.
mod test 'tools/just/test.just'

# Control a persistent interactive OMV VM.
mod vm 'tools/just/vm.just'
