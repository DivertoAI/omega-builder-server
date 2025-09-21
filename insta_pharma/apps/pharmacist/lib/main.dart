import 'package:flutter/material.dart';

void main() => runApp(const PharmacistApp());

class PharmacistApp extends StatelessWidget {
  const PharmacistApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'InstaPharma - Pharmacist',
      theme: ThemeData(useMaterial3: true, colorSchemeSeed: Colors.indigo),
      home: const DashboardScreen(),
    );
  }
}

class DashboardScreen extends StatelessWidget {
  const DashboardScreen({super.key});

  @override
  Widget build(BuildContext context) {
    final orders = [
      {'id': 'o-1001', 'customer': 'Alice', 'status': 'pending'},
      {'id': 'o-1002', 'customer': 'Bob', 'status': 'pending'},
      {'id': 'o-1003', 'customer': 'Carol', 'status': 'validated'},
    ];

    return Scaffold(
      appBar: AppBar(title: const Text('Pharmacist Dashboard')),
      body: SingleChildScrollView(
        padding: const EdgeInsets.all(16),
        child: DataTable(
          columns: const [
            DataColumn(label: Text('Order ID')),
            DataColumn(label: Text('Customer')),
            DataColumn(label: Text('Status')),
          ],
          rows: [
            for (final o in orders)
              DataRow(
                cells: [
                  DataCell(Text(o['id']!)),
                  DataCell(Text(o['customer']!)),
                  DataCell(Text(o['status']!)),
                ],
              ),
          ],
        ),
      ),
    );
  }
}
