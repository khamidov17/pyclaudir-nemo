import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:geolocator/geolocator.dart';

/// Streams GPS fixes while server-driven navigation is active.
///
/// Started on the server's `nav_start` event, stopped on `nav_stop` (or ws
/// close). Fixes go to the voice websocket as `{"type":"location",lat,lon}`
/// frames — the server's navigation watcher turns them into spoken guidance.
/// Distance filter keeps the stream quiet when standing still.
class LocationStreamer {
  StreamSubscription<Position>? _sub;

  bool get active => _sub != null;

  Future<void> start(void Function(double lat, double lon) onFix) async {
    if (_sub != null) return;
    var permission = await Geolocator.checkPermission();
    if (permission == LocationPermission.denied) {
      permission = await Geolocator.requestPermission();
    }
    if (permission == LocationPermission.denied ||
        permission == LocationPermission.deniedForever) {
      debugPrint('[nav] location permission denied — guidance unavailable');
      return;
    }
    const settings = LocationSettings(
      accuracy: LocationAccuracy.high,
      distanceFilter: 8, // meters — no spam at red lights
    );
    _sub = Geolocator.getPositionStream(locationSettings: settings).listen(
      (pos) => onFix(pos.latitude, pos.longitude),
      onError: (Object e) => debugPrint('[nav] location stream error: $e'),
    );
    debugPrint('[nav] location streaming started');
  }

  void stop() {
    _sub?.cancel();
    _sub = null;
    debugPrint('[nav] location streaming stopped');
  }
}
