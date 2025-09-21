// GENERATED: design/theme/omega_theme.dart
import 'package:flutter/material.dart';
import '../tokens/tokens.dart';
import '../fonts/google_fonts.dart';

ThemeData buildOmegaTheme({Brightness brightness = Brightness.light}) {
  final isDark = brightness == Brightness.dark;
  final base = isDark ? ThemeData.dark() : ThemeData.light();

  final primary = Color(OmegaTokens.colors.primary);
  final secondary = Color(OmegaTokens.colors.secondary);
  final bg = Color(OmegaTokens.colors.background);
  final surface = Color(OmegaTokens.colors.surface);

  final txt = OmegaFonts.textTheme(base.textTheme);

  return base.copyWith(
    useMaterial3: true,
    colorScheme: ColorScheme(
      brightness: brightness,
      primary: primary,
      onPrimary: Colors.white,
      secondary: secondary,
      onSecondary: Colors.white,
      error: Color(OmegaTokens.colors.error),
      onError: Colors.white,
      background: bg,
      onBackground: Color(OmegaTokens.colors.textPrimary),
      surface: surface,
      onSurface: Color(OmegaTokens.colors.textPrimary),
    ),
    textTheme: txt,
    scaffoldBackgroundColor: bg,
    appBarTheme: AppBarTheme(
      backgroundColor: surface,
      foregroundColor: Color(OmegaTokens.colors.textPrimary),
      elevation: OmegaTokens.elevation.level1,
      centerTitle: true,
    ),
    cardTheme: CardTheme(
      elevation: OmegaTokens.elevation.level1,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(OmegaTokens.radius.lg),
      ),
    ),
    inputDecorationTheme: InputDecorationTheme(
      border: OutlineInputBorder(
        borderRadius: BorderRadius.circular(OmegaTokens.radius.md),
      ),
    ),
  );
}
