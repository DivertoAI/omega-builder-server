import 'package:flutter/material.dart';

/// Centralized light/dark themes for Insta Pharma.
class AppTheme {
  static const _seed = Color(0xFF3555FF);

  static const TextTheme _textTheme = TextTheme(
    headlineLarge: TextStyle(fontSize: 32, fontWeight: FontWeight.w700),
    headlineMedium: TextStyle(fontSize: 24, fontWeight: FontWeight.w600),
    titleLarge: TextStyle(fontSize: 20, fontWeight: FontWeight.w600),
    bodyLarge: TextStyle(fontSize: 16),
    bodyMedium: TextStyle(fontSize: 14),
  );

  static final ThemeData light = ThemeData(
    useMaterial3: true,
    colorScheme: ColorScheme.fromSeed(
      seedColor: _seed,
      brightness: Brightness.light,
    ),
    textTheme: _textTheme,
    appBarTheme: const AppBarTheme(centerTitle: true),
    scaffoldBackgroundColor: const Color(0xFFF7F9FC),
    visualDensity: VisualDensity.adaptivePlatformDensity,
  );

  static final ThemeData dark = ThemeData(
    useMaterial3: true,
    colorScheme: ColorScheme.fromSeed(
      seedColor: _seed,
      brightness: Brightness.dark,
    ),
    textTheme: _textTheme,
    visualDensity: VisualDensity.adaptivePlatformDensity,
  );
}
