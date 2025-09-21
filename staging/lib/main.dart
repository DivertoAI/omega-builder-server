import 'package:flutter/material.dart';

void main() => runApp(const HelloOmegaApp());

class HelloOmegaApp extends StatelessWidget {
  const HelloOmegaApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'hello_omega',
      theme: ThemeData(
        colorSchemeSeed: Colors.indigo,
        useMaterial3: true,
      ),
      initialRoute: '/home',
      routes: {
        '/home': (context) => const HomeScreen(),
      },
    );
  }
}

class HomeScreen extends StatelessWidget {
  const HomeScreen({super.key});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Home')),
      body: const Center(
        child: Text(
          'Hello from Omega',
          textAlign: TextAlign.center,
        ),
      ),
    );
  }
}
