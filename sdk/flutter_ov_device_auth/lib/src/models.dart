class DeviceRegistrationRequest {
  const DeviceRegistrationRequest({
    required this.deviceId,
    required this.deviceName,
    required this.publicKeyB64u,
    this.algorithm = 'ed25519',
    this.loginHint,
    this.pinProtected = true,
    this.metadata = const {},
  });

  final String deviceId;
  final String deviceName;
  final String publicKeyB64u;
  final String algorithm;
  final String? loginHint;
  final bool pinProtected;
  final Map<String, dynamic> metadata;

  Map<String, dynamic> toJson() => {
        'device_id': deviceId,
        'device_name': deviceName,
        'public_key_b64u': publicKeyB64u,
        'algorithm': algorithm,
        'login_hint': loginHint,
        'pin_protected': pinProtected,
        'metadata': metadata,
      };
}

class DeviceChallengeStartRequest {
  const DeviceChallengeStartRequest({
    required this.tenantId,
    required this.subject,
    required this.deviceId,
  });

  final String tenantId;
  final String subject;
  final String deviceId;

  Map<String, dynamic> toJson() => {
        'tenant_id': tenantId,
        'subject': subject,
        'device_id': deviceId,
      };
}

class DeviceChallengeStartResponse {
  const DeviceChallengeStartResponse({
    required this.challengeId,
    required this.credentialId,
    required this.algorithm,
    required this.nonce,
    required this.signingInput,
    required this.expiresAt,
  });

  factory DeviceChallengeStartResponse.fromJson(Map<String, dynamic> json) {
    return DeviceChallengeStartResponse(
      challengeId: json['challenge_id'] as String,
      credentialId: json['credential_id'] as String,
      algorithm: json['algorithm'] as String,
      nonce: json['nonce'] as String,
      signingInput: json['signing_input'] as String,
      expiresAt: DateTime.parse(json['expires_at'] as String),
    );
  }

  final String challengeId;
  final String credentialId;
  final String algorithm;
  final String nonce;
  final String signingInput;
  final DateTime expiresAt;
}

class DeviceLoginResponse {
  const DeviceLoginResponse({
    required this.accessToken,
    required this.expiresIn,
    required this.subject,
    required this.tenantId,
    required this.deviceId,
    required this.roles,
  });

  factory DeviceLoginResponse.fromJson(Map<String, dynamic> json) {
    return DeviceLoginResponse(
      accessToken: json['access_token'] as String,
      expiresIn: json['expires_in'] as int,
      subject: json['subject'] as String,
      tenantId: json['tenant_id'] as String,
      deviceId: json['device_id'] as String,
      roles: ((json['roles'] as List?) ?? const []).cast<String>(),
    );
  }

  final String accessToken;
  final int expiresIn;
  final String subject;
  final String tenantId;
  final String deviceId;
  final List<String> roles;
}

class DeviceKeyMaterial {
  const DeviceKeyMaterial({
    required this.deviceId,
    required this.publicKeyB64u,
    required this.privateKeyB64u,
  });

  final String deviceId;
  final String publicKeyB64u;
  final String privateKeyB64u;
}
