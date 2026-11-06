import XCTest
@testable import MemoryOSCore

final class LocalAPIConfigurationTests: XCTestCase {
    func testBuildsLoopbackEndpoint() {
        let url = LocalAPIConfiguration.endpoint(baseURL: "http://127.0.0.1:8766", path: "/health")
        XCTAssertEqual(url?.absoluteString, "http://127.0.0.1:8766/health")
    }

    func testAllowsLocalhostAndIPv6Loopback() {
        XCTAssertNotNil(LocalAPIConfiguration.endpoint(baseURL: "http://localhost:8766", path: "/health"))
        XCTAssertNotNil(LocalAPIConfiguration.endpoint(baseURL: "http://[::1]:8766", path: "/health"))
    }

    func testRejectsRemoteAPIHosts() {
        XCTAssertNil(LocalAPIConfiguration.endpoint(baseURL: "https://example.com", path: "/health"))
        XCTAssertNil(LocalAPIConfiguration.endpoint(baseURL: "http://localhost.attacker.test", path: "/health"))
    }
}
