import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart' show rootBundle;

void main() => runApp(const CustomerApp());

class CustomerApp extends StatefulWidget {
  const CustomerApp({super.key});
  @override
  State<CustomerApp> createState() => _CustomerAppState();
}

class _CustomerAppState extends State<CustomerApp> {
  int _tab = 0;

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'InstaPharma - Customer',
      theme: ThemeData(useMaterial3: true, colorSchemeSeed: Colors.teal),
      home: Scaffold(
        appBar: AppBar(title: const Text('InstaPharma')),
        body: IndexedStack(
          index: _tab,
          children: const [
            CatalogScreen(),
            CartScreen(),
            OrdersScreen(),
            UploadRxScreen(),
          ],
        ),
        bottomNavigationBar: NavigationBar(
          selectedIndex: _tab,
          onDestinationSelected: (i) => setState(() => _tab = i),
          destinations: const [
            NavigationDestination(icon: Icon(Icons.store), label: 'Catalog'),
            NavigationDestination(
              icon: Icon(Icons.shopping_cart),
              label: 'Cart',
            ),
            NavigationDestination(
              icon: Icon(Icons.receipt_long),
              label: 'Orders',
            ),
            NavigationDestination(
              icon: Icon(Icons.upload_file),
              label: 'Upload Rx',
            ),
          ],
        ),
      ),
    );
  }
}

class CatalogScreen extends StatefulWidget {
  const CatalogScreen({super.key});
  @override
  State<CatalogScreen> createState() => _CatalogScreenState();
}

class _CatalogScreenState extends State<CatalogScreen> {
  List<Map<String, dynamic>> _products = [];
  final List<Map<String, dynamic>> _cart = [];

  @override
  void initState() {
    super.initState();
    _loadProducts();
  }

  Future<void> _loadProducts() async {
    final raw = await rootBundle.loadString('../../assets/mock/products.json');
    final List list = jsonDecode(raw);
    setState(() => _products = list.cast<Map<String, dynamic>>());
  }

  void _addToCart(Map<String, dynamic> p) {
    _cart.add(p);
    ScaffoldMessenger.of(
      context,
    ).showSnackBar(SnackBar(content: Text('Added ${p["name"]}')));
  }

  @override
  Widget build(BuildContext context) {
    if (_products.isEmpty) {
      return const Center(child: CircularProgressIndicator());
    }
    return ListView.builder(
      padding: const EdgeInsets.all(16),
      itemCount: _products.length,
      itemBuilder: (_, i) {
        final p = _products[i];
        return Card(
          child: ListTile(
            leading: const Icon(Icons.local_pharmacy),
            title: Text(p['name']),
            subtitle: Text('\$${p['price']}'),
            trailing: ElevatedButton(
              onPressed: () => _addToCart(p),
              child: const Text('Add'),
            ),
          ),
        );
      },
    );
  }
}

class CartScreen extends StatelessWidget {
  const CartScreen({super.key});
  @override
  Widget build(BuildContext context) {
    return const Center(child: Text('Cart (in-memory demo)'));
  }
}

class OrdersScreen extends StatelessWidget {
  const OrdersScreen({super.key});
  @override
  Widget build(BuildContext context) {
    return const Center(child: Text('Orders (stub)'));
  }
}

class UploadRxScreen extends StatelessWidget {
  const UploadRxScreen({super.key});
  @override
  Widget build(BuildContext context) {
    return const Center(child: Text('Upload Rx (stub)'));
  }
}
