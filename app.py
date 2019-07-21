import json
import logging
import requests
import numpy as np

from flask import Flask, request, abort
from flask.json import jsonify


app = Flask(__name__)

HERE_APP_ID='iFnDoO7BoKClHomiG04k'
HERE_APP_CODE='-vIU9uU_ey26tk5-_U-QZw'

URL_GEOCODE = 'https://geocoder.api.here.com/6.2/geocode.json?app_id={}&app_code={}&searchtext={}'
URL_CALCROUTE = 'https://route.api.here.com/routing/7.2/calculateroute.json?app_id={}&app_code={}&waypoint0={}&waypoint1={}&mode={}&returnelevation=true&routeattributes=sh'
URL_ROUTE_JPEG = 'https://image.maps.api.here.com/mia/1.6/route?app_id={}&app_code={}&r0={}&sb=m'

MAP_FOOTPRINT = {
    'car': 300,
    'e-car': 95,
    'h-car': 177,
    'bus': 66,
    'train': 45,
    'e-bike': 30,
    'bike': 0,
    'walk': 0,
}

def initialize_logging():
    logging.basicConfig(format='%(asctime)s [%(levelname)s] %(message)s',
                        datefmt='%Y/%m/%d %H:%M:%S',
                        # filename='example.log',
                        level=logging.DEBUG)

def get_geocode(place_name):
    url = URL_GEOCODE.format(HERE_APP_ID, HERE_APP_CODE, place_name)
    response = requests.get(url)
    
    if response.status_code == 200:
        res_json = response.json()
        geocode = res_json['Response']['View'][0]['Result'][0]['Location']['DisplayPosition']
        return geocode
    
    return None

def calculate_route(geocode0, geocode1, mode):
    if mode in ['car', 'publicTransport', 'bicycle', 'pedestrian']:
        waypoint0 = 'geo!{},{}'.format(geocode0['Latitude'], geocode0['Longitude'])
        waypoint1 = 'geo!{},{}'.format(geocode1['Latitude'], geocode1['Longitude'])
        mode = 'fastest;{};traffic:disabled'.format(mode)
    
        url = URL_CALCROUTE.format(HERE_APP_ID, HERE_APP_CODE, waypoint0, waypoint1, mode)
        response = requests.get(url)
        
        if response.status_code == 200:
            res_json = response.json()
            
            return res_json

    return None

def calculate_carbon_footprint(distance, mode):
    if mode in ['car', 'e-car', 'h-car', 'bus', 'train', 'walk', 'bike', 'e-bike']:
        res = MAP_FOOTPRINT[mode] * distance
        
        return int(res)
    return None

def get_xyz(fjson):
    lx = []
    ly = []
    lz = []
    for coords in fjson['response']['route'][0]['shape']:
        x, y, z = map(float, coords.split(','))
        lx.append(x)
        ly.append(y)
        lz.append(z)
    return (np.array(lx), np.array(ly), np.array(lz))

def calculate_bike_eta(json_route, eta_minutes, mode):
    def get_z(fjson):
        lz = []
        for coords in fjson['response']['route'][0]['shape']:
            x, y, z = map(float, coords.split(','))
            lz.append(z)
        return lz

    eta_minutes = eta_minutes
    lz = get_z(json_route)
    zdiff = np.diff(lz)

    if mode == 'bike':
        eta_minutes *= np.mean(1.14 ** zdiff)

    if mode == 'e-bike':
        eta_minutes *= 0.8

    return int(eta_minutes)

def calc_price(product, miles, minutes):
    map_pricing = {
        'uber_x': {
            'base_fare': 2.75,
            'per_mile': 0.84,
            'per_minute': 0.36,
            'min_fare': 7.70,
            'multiplier': 1.2,
        },
        'uber_pool': {
            'base_fare': 2.70,
            'per_mile': 0.80,
            'per_minute': 0.26,
            'min_fare': 8.15,
            'multiplier': 0.8,
        },
        'jump': {
            'base_fare': 1.00,
            'per_mile': 0.0,
            'per_minute': 0.15,
            'min_fare': None,
            'multiplier': 1,
        },
    }

    price = 0.0
    if product in map_pricing:
        price = map_pricing[product]['base_fare']
        price += miles * map_pricing[product]['per_mile']
        price += minutes * map_pricing[product]['per_minute']
        price *= map_pricing[product]['multiplier']
        if map_pricing[product]['min_fare'] is not None:
            price = np.max((price, map_pricing[product]['min_fare']))
    
    return np.round(price, 2)

def compose_route_jpeg_url(json_route):
    lx, ly, _ = get_xyz(json_route)

    idx = list(map(int, np.trunc(np.linspace(0, len(lx)-1, 20))))

    route_str = ','.join(map(lambda x: '{},{}'.format(x[0], x[1]), zip(lx[idx], ly[idx])))
#    route = []
#    for maneuver in json_route['response']['route'][0]['leg'][0]['maneuver']:
#        route.append('{},{}'.format(maneuver['position']['latitude'], maneuver['position']['longitude']))
#    
#    route_str = ','.join(route)

    url = URL_ROUTE_JPEG.format(HERE_APP_ID, HERE_APP_CODE, route_str)

    return url

@app.route('/api/v1/route_stats/', methods=['POST'])
def route_stats():
    request_json = request.get_json()
    if not request_json:
        logging.info('Request is not a JSON')
        abort(400, 'Request is not a JSON')

    if ('PlaceName0' not in request_json['data']) or ('PlaceName1' not in request_json['data']):
        logging.info('PlaceName0 or PlaceName1 not in JSON')
        abort(400, 'PlaceName0 or PlaceName1 not in JSON')

    print(request_json)

    place_name0 = request_json['data']['PlaceName0']
    place_name1 = request_json['data']['PlaceName1']

    geo0 = get_geocode(request_json['data']['PlaceName0'])
    geo1 = get_geocode(request_json['data']['PlaceName1'])

    print(geo0)
    print(geo1)

    if (geo0 is None) or (geo1 is None):
        abort(400, 'Could not obtain geolocalization for PlaceName0 or PlaceName1')

    stats = {}
    jpeg_routes = {}
    for mode in ['car', 'publicTransport', 'bicycle']:
        route = calculate_route(geo0, geo1, mode)

        with open('/tmp/{}.json'.format(mode), 'w') as fh:
            json.dump(route, fh)

        summary = route['response']['route'][0]['summary']
        distance_kms = np.round(summary['distance'] / 1000, 2)
        distance_miles = np.round(distance_kms / 1.6, 2)
        eta_minutes = int(summary['baseTime'] / 60)

        jpeg_routes[mode] = compose_route_jpeg_url(route)

        if mode == 'car':
            stats['car'] = {
                'distance': distance_miles,
                'eta': eta_minutes,
                'footprint': calculate_carbon_footprint(distance_kms, 'car'),
                'price': calc_price('uber_x', distance_miles, eta_minutes)
            }
            stats['car-pool'] = {
                'distance': distance_miles,
                'eta': eta_minutes,
                'footprint': int(calculate_carbon_footprint(distance_kms, 'car') / 3),
                'price': calc_price('uber_pool', distance_miles, eta_minutes)
            }
            stats['e-car'] = {
                'distance': distance_miles,
                'eta': eta_minutes,
                'footprint': calculate_carbon_footprint(distance_kms, 'e-car'),
                'price': 0.0,
            }
            stats['h-car'] = {
                'distance': distance_miles,
                'eta': eta_minutes,
                'footprint': calculate_carbon_footprint(distance_kms, 'h-car'),
                'price': 0.0,
            }
        if mode == 'publicTransport':
            stats['bus'] = {
                'distance': distance_miles,
                'eta': eta_minutes,
                'footprint': calculate_carbon_footprint(distance_kms, 'bus'),
                'price': 2.25,
            }
        if mode == 'bicycle':
            stats['bike'] = {
                'distance': distance_miles,
                'eta': calculate_bike_eta(route, eta_minutes, 'bike'),
                'footprint': calculate_carbon_footprint(distance_kms, 'bike'),
                'price': 0.0,
            }
            stats['e-bike'] = {
                'distance': distance_miles,
                'eta': calculate_bike_eta(route, eta_minutes, 'e-bike'),
                'footprint': calculate_carbon_footprint(distance_kms, 'e-bike'),
                'price': calc_price('jump', distance_miles, eta_minutes)
            }
        
    return jsonify({'data': {'statistics': stats, 'routes': jpeg_routes, 'summary': {'geo0': geo0, 'geo1': geo1}}})

if __name__ == '__main__':
    # local only
    app.run(host='0.0.0.0', port=8080, debug=True)
#    app.run(host='0.0.0.0', debug=True)

    initialize_logging()
