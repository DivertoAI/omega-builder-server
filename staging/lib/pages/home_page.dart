import 'package:flutter/material.dart';

class HomePage extends StatelessWidget {
  const HomePage({super.key});

  static const List<String> items = ['Alpha', 'Beta', 'Gamma', 'Delta'];

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Home')),
      body: ListView.separated(
        padding: const EdgeInsets.all(12),
        itemCount: items.length,
        separatorBuilder: (_, __) => const SizedBox(height: 8),
        itemBuilder: (context, index) {
          final title = items[index];
          return Card(
            child: ListTile(
              title: Text(title),
              trailing: const Icon(Icons.chevron_right),
              onTap: () => Navigator.pushNamed(
                context,
                '/detail',
                arguments: {'title': title},
              ),
            ),
          );
        },
      ),
    );
  }
}
