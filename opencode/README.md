# sshc Agent Bridge for OpenCode

This package reports OpenCode session state to the sshc terminal pane that is
already running OpenCode. It has no runtime dependencies and performs no
network requests.

Until the package is published to npm, clone this repository and add the
absolute `opencode/index.js` path to the `plugin` array in `opencode.json`.
