import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

/// Light palette — clean white with a two-blue accent (cornflower → electric).
/// Every color in the app routes through these constants. No scattered hex.
class NemoColors {
  NemoColors._();

  /// App background — soft blue-tinted white.
  static const bg = Color(0xFFF3F6FE);

  /// Raised surface (cards, fields) — pure white.
  static const surface = Color(0xFFFFFFFF);

  /// Hairline borders / dividers.
  static const border = Color(0xFFDDE4F1);

  /// Signature accent — deep electric blue (the right bar).
  static const accent = Color(0xFF2233F0);

  /// Secondary accent — lighter cornflower blue (the left bar).
  static const accent2 = Color(0xFF4A82F7);

  /// Nemo is speaking — a vivid blue between the two.
  static const speaking = Color(0xFF3D5BFF);

  /// Danger (panic / destructive).
  static const danger = Color(0xFFEF4444);

  /// Text tiers (dark on light).
  static const text = Color(0xFF141A2E);
  static const textDim = Color(0xFF5A6478);
  static const textFaint = Color(0xFF98A2B5);
}

/// Light Material3 theme factory. Wired into MaterialApp by main.dart.
ThemeData buildNemoTheme() {
  final base = ColorScheme.fromSeed(
    seedColor: NemoColors.accent,
    brightness: Brightness.light,
  ).copyWith(
    primary: NemoColors.accent,
    secondary: NemoColors.accent2,
    surface: NemoColors.surface,
    error: NemoColors.danger,
  );

  return ThemeData(
    useMaterial3: true,
    colorScheme: base,
    scaffoldBackgroundColor: NemoColors.bg,
    fontFamily: 'Roboto',
    appBarTheme: const AppBarTheme(
      backgroundColor: NemoColors.bg,
      elevation: 0,
      centerTitle: false,
      foregroundColor: NemoColors.text,
      systemOverlayStyle: SystemUiOverlayStyle.dark,
      titleTextStyle: TextStyle(
        color: NemoColors.text,
        fontSize: 18,
        fontWeight: FontWeight.w600,
        letterSpacing: 0.3,
      ),
    ),
    textTheme: const TextTheme(
      headlineMedium: TextStyle(
        color: NemoColors.text,
        fontWeight: FontWeight.w700,
        letterSpacing: -0.5,
      ),
      titleMedium: TextStyle(color: NemoColors.text, letterSpacing: 0.2),
      bodyMedium: TextStyle(color: NemoColors.textDim, letterSpacing: 0.2),
    ),
    dividerColor: NemoColors.border,
    inputDecorationTheme: InputDecorationTheme(
      filled: true,
      fillColor: NemoColors.surface,
      hintStyle: const TextStyle(color: NemoColors.textFaint),
      labelStyle: const TextStyle(color: NemoColors.textDim),
      helperStyle: const TextStyle(color: NemoColors.textFaint, fontSize: 12),
      helperMaxLines: 3,
      contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 16),
      border: OutlineInputBorder(
        borderRadius: BorderRadius.circular(14),
        borderSide: const BorderSide(color: NemoColors.border),
      ),
      enabledBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(14),
        borderSide: const BorderSide(color: NemoColors.border),
      ),
      focusedBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(14),
        borderSide: const BorderSide(color: NemoColors.accent, width: 1.5),
      ),
    ),
    filledButtonTheme: FilledButtonThemeData(
      style: FilledButton.styleFrom(
        backgroundColor: NemoColors.accent,
        foregroundColor: Colors.white,
        padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 16),
        textStyle: const TextStyle(
          fontWeight: FontWeight.w600,
          letterSpacing: 0.3,
          fontSize: 15,
        ),
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(14),
        ),
      ),
    ),
    outlinedButtonTheme: OutlinedButtonThemeData(
      style: OutlinedButton.styleFrom(
        foregroundColor: NemoColors.accent,
        side: const BorderSide(color: NemoColors.border),
        padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 16),
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(14),
        ),
      ),
    ),
    textButtonTheme: TextButtonThemeData(
      style: TextButton.styleFrom(foregroundColor: NemoColors.accent),
    ),
    snackBarTheme: const SnackBarThemeData(
      backgroundColor: NemoColors.text,
      contentTextStyle: TextStyle(color: Colors.white),
      behavior: SnackBarBehavior.floating,
    ),
    dialogTheme: DialogThemeData(
      backgroundColor: NemoColors.surface,
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(18)),
      titleTextStyle: const TextStyle(
        color: NemoColors.text,
        fontSize: 18,
        fontWeight: FontWeight.w600,
      ),
      contentTextStyle: const TextStyle(color: NemoColors.textDim),
    ),
    listTileTheme: const ListTileThemeData(
      iconColor: NemoColors.accent,
      textColor: NemoColors.text,
    ),
    switchTheme: SwitchThemeData(
      thumbColor: WidgetStateProperty.resolveWith(
        (s) => s.contains(WidgetState.selected)
            ? NemoColors.accent
            : NemoColors.textFaint,
      ),
      trackColor: WidgetStateProperty.resolveWith(
        (s) => s.contains(WidgetState.selected)
            ? NemoColors.accent.withValues(alpha: 0.3)
            : NemoColors.border,
      ),
    ),
  );
}
