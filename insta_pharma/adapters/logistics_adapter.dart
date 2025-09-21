// adapters/logistics_adapter.dart
class LogisticsAdapter {
  static const _provider = String.fromEnvironment('LOGISTICS_PROVIDER', defaultValue: 'mock');
  static bool get enabled => _provider != 'disabled';

  Future<String> quote({required double kg, required String toZip}) async {
    if (!enabled) return 'disabled';
    // TODO: call provider
    return 'quote:$_provider:$kg:$toZip';
  }
}
