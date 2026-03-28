import 'dart:convert';
import 'dart:math';

import 'package:cryptography/cryptography.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';

import 'models.dart';

class SecureStorageEd25519KeyStore {
  SecureStorageEd25519KeyStore({
    FlutterSecureStorage? storage,
    Cryptography? cryptography,
  })  : _storage = storage ?? const FlutterSecureStorage(),
        _algorithm = (cryptography ?? Cryptography.instance).ed25519();

  final FlutterSecureStorage _storage;
  final Ed25519 _algorithm;

  Future<DeviceKeyMaterial> loadOrCreate({required String deviceId}) async {
    final privateKey = await _storage.read(key: 'ov_device_private_$deviceId');
    final publicKey = await _storage.read(key: 'ov_device_public_$deviceId');
    if (privateKey != null && publicKey != null) {
      return DeviceKeyMaterial(
        deviceId: deviceId,
        publicKeyB64u: publicKey,
        privateKeyB64u: privateKey,
      );
    }

    final keyPair = await _algorithm.newKeyPair();
    final privateData = await keyPair.extract();
    final publicData = await keyPair.extractPublicKey();

    final privateB64u = _b64uEncode(privateData.bytes);
    final publicB64u = _b64uEncode(publicData.bytes);
    await _storage.write(key: 'ov_device_private_$deviceId', value: privateB64u);
    await _storage.write(key: 'ov_device_public_$deviceId', value: publicB64u);

    return DeviceKeyMaterial(
      deviceId: deviceId,
      publicKeyB64u: publicB64u,
      privateKeyB64u: privateB64u,
    );
  }

  Future<String> sign({
    required DeviceKeyMaterial keyMaterial,
    required String signingInput,
  }) async {
    final keyPair = SimpleKeyPairData(
      _b64uDecode(keyMaterial.privateKeyB64u),
      publicKey: SimplePublicKey(
        _b64uDecode(keyMaterial.publicKeyB64u),
        type: KeyPairType.ed25519,
      ),
      type: KeyPairType.ed25519,
    );
    final signature = await _algorithm.sign(
      utf8.encode(signingInput),
      keyPair: keyPair,
    );
    return _b64uEncode(signature.bytes);
  }

  static String generateDeviceId() {
    final random = Random.secure();
    final bytes = List<int>.generate(16, (_) => random.nextInt(256));
    return _b64uEncode(bytes);
  }

  static String _b64uEncode(List<int> value) =>
      base64UrlEncode(value).replaceAll('=', '');

  static List<int> _b64uDecode(String value) {
    final padded = value.padRight((value.length + 3) ~/ 4 * 4, '=');
    return base64Url.decode(padded);
  }
}
