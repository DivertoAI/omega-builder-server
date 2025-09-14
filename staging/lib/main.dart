import 'package:flutter/material.dart';

void main() {
  runApp(const HelloButtonApp());
}

class HelloButtonApp extends StatelessWidget {
  const HelloButtonApp({super.key});

  @override
  Widget build(BuildContext context) {
    final radius10 = BorderRadius.circular(10);
    return MaterialApp(
      title: 'Hello Button App',
      debugShowCheckedModeBanner: false,
      theme: ThemeData(
        colorSchemeSeed: Colors.blue,
        elevatedButtonTheme: ElevatedButtonThemeData(
          style: ElevatedButton.styleFrom(
            shape: RoundedRectangleBorder(borderRadius: radius10),
          ),
        ),
      ),
      initialRoute: '/home',
      routes: {
        '/home': (context) => const HomePage(),
      },
    );
  }
}

class HomePage extends StatelessWidget {
  const HomePage({super.key});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Home')),
      body: Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Text('Hello', style: Theme.of(context).textTheme.headlineMedium),
            const SizedBox(height: 16),
            ElevatedButton(
              onPressed: () {
                ScaffoldMessenger.of(context).showSnackBar(
                  const SnackBar(content: Text('Button pressed')),
                );
              },
              child: const Text('Press me'),
            ),
          ],
        ),
      ),
    );
  }
}
