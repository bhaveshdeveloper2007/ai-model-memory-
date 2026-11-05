import CryptoKit
import Foundation
import Security

let prefix = Data("MEMORYOS-AESGCM-1\n".utf8)
func fail(_ message: String) -> Never { FileHandle.standardError.write(Data((message + "\n").utf8)); exit(1) }
func serviceName() -> String {
    guard CommandLine.arguments.count >= 3 else { fail("Usage: memoryos-crypto <init|key|import|encrypt|decrypt> <service>") }
    return CommandLine.arguments[2]
}
func query(_ service: String) -> [String: Any] {
    [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service, kSecAttrAccount as String: "snapshot-key", kSecReturnData as String: true, kSecMatchLimit as String: kSecMatchLimitOne]
}
func loadKey(_ service: String) -> Data? {
    var item: CFTypeRef?
    let status = SecItemCopyMatching(query(service) as CFDictionary, &item)
    guard status == errSecSuccess, let data = item as? Data, data.count == 32 else { return nil }
    return data
}
func saveKey(_ key: Data, _ service: String) -> OSStatus {
    let attrs: [String: Any] = [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service, kSecAttrAccount as String: "snapshot-key", kSecValueData as String: key, kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly]
    let status = SecItemAdd(attrs as CFDictionary, nil)
    if status == errSecDuplicateItem { return errSecSuccess }
    return status
}
let service = serviceName()
let command = CommandLine.arguments[1]
switch command {
case "init":
    let key = loadKey(service) ?? SymmetricKey(size: .bits256).withUnsafeBytes { Data($0) }
    let status = saveKey(key, service)
    guard status == errSecSuccess, let stored = loadKey(service) else { fail("Could not initialize the Memory OS key in macOS Keychain (status \(status)).") }
    print(stored.base64EncodedString())
case "key":
    guard let key = loadKey(service) else { fail("Memory OS encryption key is not in Keychain.") }
    print(key.base64EncodedString())
case "import":
    let input = FileHandle.standardInput.readDataToEndOfFile()
    guard let string = String(data: input, encoding: .utf8), let key = Data(base64Encoded: string.trimmingCharacters(in: .whitespacesAndNewlines)), key.count == 32 else { fail("Recovery key must be a base64 encoded 256-bit key.") }
    let status = saveKey(key, service)
    guard status == errSecSuccess, loadKey(service) == key else { fail("Could not import the recovery key into macOS Keychain (status \(status)).") }
    print("Recovery key imported.")
case "encrypt", "decrypt":
    guard let keyData = loadKey(service) else { fail("Memory OS encryption key is not in Keychain.") }
    let symmetric = SymmetricKey(data: keyData)
    let input = FileHandle.standardInput.readDataToEndOfFile()
    if command == "encrypt" {
        do {
            let sealed = try AES.GCM.seal(input, using: symmetric)
            guard let combined = sealed.combined else { fail("Could not encode encrypted snapshot.") }
            FileHandle.standardOutput.write(prefix + combined)
        } catch { fail("Encryption failed: \(error)") }
    } else {
        guard input.starts(with: prefix) else { fail("Not a Memory OS encrypted backup.") }
        do {
            let box = try AES.GCM.SealedBox(combined: input.dropFirst(prefix.count))
            let clear = try AES.GCM.open(box, using: symmetric)
            FileHandle.standardOutput.write(clear)
        } catch { fail("Decryption or authentication failed: \(error)") }
    }
default: fail("Unknown Memory OS crypto action.")
}
