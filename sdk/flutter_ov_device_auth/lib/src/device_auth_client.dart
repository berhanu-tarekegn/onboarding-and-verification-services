import 'dart:convert';

import 'package:http/http.dart' as http;

import 'device_key_store.dart';
import 'models.dart';

class DeviceAuthClient {
  DeviceAuthClient({
    required this.baseUrl,
    required this.keyStore,
    http.Client? httpClient,
  }) : _http = httpClient ?? http.Client();

  final String baseUrl;
  final SecureStorageEd25519KeyStore keyStore;
  final http.Client _http;

  Future<void> registerDevice({
    required String bearerToken,
    required String deviceId,
    required String deviceName,
    String? loginHint,
    Map<String, dynamic> metadata = const {},
  }) async {
    final keys = await keyStore.loadOrCreate(deviceId: deviceId);
    final payload = DeviceRegistrationRequest(
      deviceId: deviceId,
      deviceName: deviceName,
      publicKeyB64u: keys.publicKeyB64u,
      loginHint: loginHint,
      metadata: metadata,
    );
    final response = await _http.post(
      Uri.parse('$baseUrl/api/auth/device/registrations'),
      headers: {
        'Authorization': 'Bearer $bearerToken',
        'Content-Type': 'application/json',
      },
      body: jsonEncode(payload.toJson()),
    );
    _ensureSuccess(response);
  }

  Future<DeviceLoginResponse> loginWithDevice({
    required String tenantId,
    required String subject,
    required String deviceId,
    Future<void> Function()? requireLocalUnlock,
  }) async {
    final challengeStart = await _http.post(
      Uri.parse('$baseUrl/api/auth/device/challenges/start'),
      headers: {'Content-Type': 'application/json'},
      body: jsonEncode(
        DeviceChallengeStartRequest(
          tenantId: tenantId,
          subject: subject,
          deviceId: deviceId,
        ).toJson(),
      ),
    );
    _ensureSuccess(challengeStart);
    final challenge = DeviceChallengeStartResponse.fromJson(
      jsonDecode(challengeStart.body) as Map<String, dynamic>,
    );

    if (requireLocalUnlock != null) {
      await requireLocalUnlock();
    }

    final keys = await keyStore.loadOrCreate(deviceId: deviceId);
    final signature = await keyStore.sign(
      keyMaterial: keys,
      signingInput: challenge.signingInput,
    );

    final complete = await _http.post(
      Uri.parse('$baseUrl/api/auth/device/challenges/complete'),
      headers: {'Content-Type': 'application/json'},
      body: jsonEncode({
        'challenge_id': challenge.challengeId,
        'signature_b64u': signature,
      }),
    );
    _ensureSuccess(complete);
    return DeviceLoginResponse.fromJson(
      jsonDecode(complete.body) as Map<String, dynamic>,
    );
  }

  static void _ensureSuccess(http.Response response) {
    if (response.statusCode >= 200 && response.statusCode < 300) {
      return;
    }
    throw DeviceAuthException(
      'Device auth request failed: ${response.statusCode}',
      response.body,
    );
  }
}

class DeviceAuthException implements Exception {
  DeviceAuthException(this.message, this.body);

  final String message;
  final String body;

  @override
  String toString() => '$message\n$body';
}
