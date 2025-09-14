import 'package:flutter/material.dart';

void main() {
  runApp(const FlutterListDetailApp());
}

class FlutterListDetailApp extends StatelessWidget {
  const FlutterListDetailApp({super.key});

  @override
  Widget build(BuildContext context) {
    final theme = ThemeData(
      useMaterial3: true,
      cardTheme: CardTheme(
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(10), // matches spec radius[1]
        ),
      ),
      listTileTheme: const ListTileThemeData(
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.all(Radius.circular(6)), // matches spec radius[0]
        ),
      ),
    );

    return MaterialApp(
      title: 'FlutterListDetailApp',
      theme: theme,
      initialRoute: '/',
      onGenerateRoute: (settings) {
        final name = settings.name ?? '/';
        if (name == '/') {
          return MaterialPageRoute(builder: (_) => HomePage(items: kItems));
        }
        final uri = Uri.parse(name);
        if (uri.pathSegments.length == 2 && uri.pathSegments.first == 'items') {
          final id = int.tryParse(uri.pathSegments[1]);
          if (id != null) {
            final item = kItems.firstWhere(
              (e) => e.id == id,
              orElse: () => Item(
                id: id,
                title: 'Item $id',
                subtitle: 'Generated item',
                description: 'Details for Item $id (generated on the fly).',
              ),
            );
            return MaterialPageRoute(builder: (_) => DetailPage(item: item));
          }
        }
        return MaterialPageRoute(
          builder: (_) => Scaffold(
            appBar: AppBar(title: const Text('Not found')),
            body: Center(
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Text('Route not found: $name'),
                  const SizedBox(height: 12),
                  FilledButton(
                    onPressed: () => Navigator.of(_).pushNamedAndRemoveUntil('/', (r) => false),
                    child: const Text('Go Home'),
                  ),
                ],
              ),
            ),
          ),
        );
      },
    );
  }
}

class Item {
  final int id;
  final String title;
  final String subtitle;
  final String description;
  const Item({required this.id, required this.title, required this.subtitle, required this.description});
}

final List<Item> kItems = List.generate(
  20,
  (i) => Item(
    id: i + 1,
    title: 'Item ${i + 1}',
    subtitle: 'Subtitle for item ${i + 1}',
    description: 'This is a simple detail page for item ${i + 1}.',
  ),
);

class HomePage extends StatelessWidget {
  final List<Item> items;
  const HomePage({super.key, required this.items});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Items')),
      body: ListView.separated(
        padding: const EdgeInsets.all(12),
        itemCount: items.length,
        separatorBuilder: (_, __) => const SizedBox(height: 8),
        itemBuilder: (context, index) {
          final item = items[index];
          return Material(
            color: Theme.of(context).colorScheme.surface,
            borderRadius: BorderRadius.circular(6),
            child: ListTile(
              title: Text(item.title),
              subtitle: Text(item.subtitle),
              trailing: const Icon(Icons.chevron_right),
              onTap: () => Navigator.pushNamed(context, '/items/${item.id}'),
            ),
          );
        },
      ),
    );
  }
}

class DetailPage extends StatelessWidget {
  final Item item;
  const DetailPage({super.key, required this.item});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: Text(item.title)),
      body: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(item.subtitle, style: Theme.of(context).textTheme.titleMedium),
            const SizedBox(height: 12),
            Text(item.description),
            const Spacer(),
            FilledButton.icon(
              onPressed: () => Navigator.pop(context),
              icon: const Icon(Icons.arrow_back),
              label: const Text('Back'),
            ),
          ],
        ),
      ),
    );
  }
}
