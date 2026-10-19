import 'package:flutter/material.dart';

typedef Json = Map<String, dynamic>;

void main() {
  runApp(const MyApp());
}

String greet(String name) => 'Hello, ${name.toUpperCase()}! {not a block}';

int get answer => 42;

/// The root widget.
class MyApp extends StatelessWidget {
  const MyApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(home: Text('''
multi { line
${greet("x")} }
'''));
  }
}

abstract class Shape {
  double area();
}

sealed class Result {}

class Counter extends StatefulWidget {
  const Counter({Key? key, this.start = 0}) : super(key: key);

  final int start;

  @override
  State<Counter> createState() => _CounterState();
}

class _CounterState extends State<Counter> {
  int _count = 0;

  _CounterState();

  _CounterState.named(int c) : _count = c;

  factory _CounterState.zero() {
    return _CounterState.named(0);
  }

  int get count => _count;

  set count(int v) {
    _count = v;
  }

  void _inc() {
    // a } in a comment
    setState(() {
      _count++;
    });
  }

  @override
  Widget build(BuildContext context) {
    final label = "count: ${_count > 0 ? '}' : "{"}";
    return ElevatedButton(onPressed: _inc, child: Text(label));
  }
}

mixin Logger on Object {
  void log(String m) => print(m);
}

extension StringX on String {
  String shout() => toUpperCase() + '!';
}

extension on int {
  int get doubled => this * 2;
}

enum Color {
  red(1),
  green(2);

  const Color(this.code);

  final int code;

  String label() => name;
}
