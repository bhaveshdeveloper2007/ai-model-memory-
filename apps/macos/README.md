# Native macOS app status

The initial SwiftUI menu-bar companion is under construction. The current host has Swift 6.4.2 and Apple Silicon Command Line Tools, but the active developer directory is Command Line Tools and `xcodebuild` reports that full Xcode is required. Source can be reviewed, but an app build and XCTest run cannot be verified here. The test target includes loopback URL allowlist checks.

When full Xcode is selected, use `scripts/build-macos-app.sh` to build and `scripts/run-macos-app.sh` to launch.

Planned first UI: menu-bar companion with an expandable project activity view, backed only by real API data. It will read its API credential from macOS Keychain; it will not read the development `.env` file. See `docs/IMPLEMENTATION_PLAN.md`.
