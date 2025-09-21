import "package:flutter/material.dart";

class AppTheme {
  static ThemeData get light => ThemeData(
    colorScheme: ColorScheme.fromSeed(seedColor: Color(0xFF3555FF)),
    useMaterial3: true,
    visualDensity: VisualDensity.adaptivePlatformDensity,
    textTheme: TextTheme(titleLarge: TextStyle(fontWeight: FontWeight.w600)),
  );
}
