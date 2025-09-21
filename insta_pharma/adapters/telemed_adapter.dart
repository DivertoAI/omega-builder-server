// adapters/telemed_adapter.dart
class TelemedAdapter {
  static const _key = String.fromEnvironment('TELEMED_API_KEY', defaultValue: '');
  static bool get enabled => _key.isNotEmpty;

  Future<String> createVisit(String patientId) async {
    if (!enabled) return 'disabled';
    // TODO: call provider
    return 'visit-created:$patientId';
  }
}
