// GENERATED: design/fonts/google_fonts.dart
import 'package:flutter/material.dart';

/// Thin wrapper so apps can swap in google_fonts without hard dependency.
/// If you add `google_fonts` to pubspec, you can replace this implementation.
class OmegaFonts {
  static TextTheme textTheme([TextTheme? base]) {
    final b = base ?? ThemeData.light().textTheme;
    return b.apply(fontFamily: 'Inter');
  }

  static String primaryFamily() => 'Inter';
}
