import Foundation

public enum LocalAPIConfiguration {
    private static let allowedHosts: Set<String> = ["localhost", "127.0.0.1", "::1"]

    public static func endpoint(baseURL: String, path: String) -> URL? {
        guard let base = URL(string: baseURL),
              let host = base.host?.lowercased(),
              allowedHosts.contains(host),
              ["http", "https"].contains(base.scheme?.lowercased() ?? "") else {
            return nil
        }
        return URL(string: path, relativeTo: base)?.absoluteURL
    }
}
