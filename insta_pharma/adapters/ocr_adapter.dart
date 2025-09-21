// adapters/ocr_adapter.dart
class OcrAdapter {
  static const _provider = String.fromEnvironment('OCR_PROVIDER', defaultValue: 'mock');
  static bool get enabled => _provider != 'disabled';

  Future<String> extractText(List<int> imageBytes) async {
    if (!enabled) return 'disabled';
    // TODO: call provider
    return 'ok:$_provider:len=${imageBytes.length}';
  }
}
