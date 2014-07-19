"""
Constants and definitions.
Now we need to specify all the names we know for water level, names that
will get used in the CSW search, and also to find data in the datasets that
are returned.  This is ugly and fragile.  There hopefully will be a better
way in the future...
Standard Library.
"""

from lxml import etree
from io import BytesIO
try:
    from urllib import urlopen
except ImportError:
    from urllib.request import urlopen

# Scientific stack.
import iris
import numpy as np
import numpy.ma as ma
from iris.unit import Unit
from pandas import read_csv
from scipy.spatial import KDTree
from IPython.display import HTML
from iris.exceptions import CoordinateNotFoundError

# Custom IOOS/ASA modules (available at PyPI).
from owslib import fes


CSW = {'NGDC Geoportal':
       'http://www.ngdc.noaa.gov/geoportal/csw',
       'USGS WHSC Geoportal':
       'http://geoport.whoi.edu/geoportal/csw',
       'NODC Geoportal: granule level':
       'http://www.nodc.noaa.gov/geoportal/csw',
       'NODC Geoportal: collection level':
       'http://data.nodc.noaa.gov/geoportal/csw',
       'NRCAN CUSTOM':
       'http://geodiscover.cgdi.ca/wes/serviceManagerCSW/csw',
       'USGS Woods Hole GI_CAT':
       'http://geoport.whoi.edu/gi-cat/services/cswiso',
       'USGS CIDA Geonetwork':
       'http://cida.usgs.gov/gdp/geonetwork/srv/en/csw',
       'USGS Coastal and Marine Program':
       'http://cmgds.marine.usgs.gov/geonetwork/srv/en/csw',
       'USGS Woods Hole Geoportal':
       'http://geoport.whoi.edu/geoportal/csw',
       'CKAN testing site for new Data.gov':
       'http://geo.gov.ckan.org/csw',
       'EPA':
       'https://edg.epa.gov/metadata/csw',
       'CWIC':
       'http://cwic.csiss.gmu.edu/cwicv1/discovery'}

titles = dict({'http://omgsrv1.meas.ncsu.edu:8080/thredds/dodsC/fmrc/sabgom/'
               'SABGOM_Forecast_Model_Run_Collection_best.ncd': 'SABGOM',
               'http://geoport.whoi.edu/thredds/dodsC/coawst_4/use/fmrc/'
               'coawst_4_use_best.ncd': 'COAWST_4',
               'http://tds.marine.rutgers.edu/thredds/dodsC/roms/espresso/'
               '2013_da/his_Best/'
               'ESPRESSO_Real-Time_v2_History_Best_Available_best.ncd':
               'ESPRESSO',
               'http://oos.soest.hawaii.edu/thredds/dodsC/hioos/tide_pac':
               'BTMPB',
               'http://opendap.co-ops.nos.noaa.gov/thredds/dodsC/TBOFS/fmrc/'
               'Aggregated_7_day_TBOFS_Fields_Forecast_best.ncd': 'TBOFS',
               'http://oos.soest.hawaii.edu/thredds/dodsC/pacioos/hycom/'
               'global': 'HYCOM',
               'http://opendap.co-ops.nos.noaa.gov/thredds/dodsC/CBOFS/fmrc/'
               'Aggregated_7_day_CBOFS_Fields_Forecast_best.ncd': 'CBOFS',
               'http://geoport-dev.whoi.edu/thredds/dodsC/estofs/atlantic':
               'ESTOFS',
               'http://www.smast.umassd.edu:8080/thredds/dodsC/FVCOM/NECOFS/'
               'Forecasts/NECOFS_GOM3_FORECAST.nc': 'NECOFS_GOM3_FVCOM',
               'http://www.smast.umassd.edu:8080/thredds/dodsC/FVCOM/NECOFS/'
               'Forecasts/NECOFS_WAVE_FORECAST.nc': 'NECOFS_GOM3_WAVE'})


def get_model_name(cube, url):
    try:
        model_full_name = cube.attributes['title']
    except AttributeError:
        model_full_name = url
    try:
        mod_name = titles[url]
    except KeyError:
        print('Model %s not in the list' % url)
        mod_name = model_full_name
    return mod_name, model_full_name


def get_cube(url, constraint, jd_start, jd_stop):
    """Load cube, check units and return a
    time-sliced cube to reduce download."""
    cube = iris.load_cube(url, constraint)
    if not cube.units == Unit('meters'):
        # TODO: Isn't working for unstructured data.
        cube.convert_units('m')
    timevar = find_timevar(cube)
    start = timevar.units.date2num(jd_start)
    istart = timevar.nearest_neighbour_index(start)
    stop = timevar.units.date2num(jd_stop)
    istop = timevar.nearest_neighbour_index(stop)
    if istart == istop:
        raise(ValueError)
    return cube[istart:istop]


def wrap_lon180(lon):
    lon = np.atleast_1d(lon).copy()
    angles = np.logical_or((lon < -180), (180 < lon))
    lon[angles] = wrap_lon360(lon[angles] + 180) - 180
    return lon


def wrap_lon360(lon):
    lon = np.atleast_1d(lon).copy()
    positive = lon > 0
    lon = lon % 360
    lon[np.logical_and(lon == 0, positive)] = 360
    return lon


def make_tree(cube):
    """Create KDTree."""
    lon = cube.coord(axis='X').points
    lat = cube.coord(axis='Y').points
    lon = wrap_lon180(lon)
    # Structured models with 1D lon, lat.
    if (lon.ndim == 1) and (lat.ndim == 1) and (cube.ndim == 3):
        lon, lat = np.meshgrid(lon, lat)
    # Unstructure are already paired!
    tree = KDTree(zip(lon.ravel(), lat.ravel()))
    return tree, lon, lat


def get_nearest_water(cube, tree, xi, yi, k=10,
                      max_dist=0.04, min_var=0.01):
    """Find `k` nearest model data points from an iris `cube` at station
    lon=`xi`, lat=`yi` up to `max_dist` in degrees.  Must provide a Scipy's
    KDTree `tree`."""
    # TODO: pykdtree might be faster, but would introduce another dependency.
    # Scipy is more likely to be already installed.  Still, this function could
    # be generalized to accept pykdtree tree object.
    # TODO: Make the `tree` optional, so it can be created here in case of a
    #  single search.  However, make sure that it will be faster if the `tree`
    #  is created and passed as an argument in multiple searches.
    distances, indices = tree.query(np.array([xi, yi]).T, k=k)
    if indices.size == 0:
        raise ValueError("No data found.")
    # Get data up to specified distance.
    mask = distances <= max_dist
    if mask is None:
        raise ValueError("No data found for (%s,%s) using max_dist=%s." %
                         (xi, yi, max_dist))
    distances, indices = distances[mask], indices[mask]
    # Unstructured model.
    if (cube.coord(axis='X').ndim == 1) and (cube.ndim == 2):
        i = j = indices
    # Structured model.
    else:
        i, j = np.unravel_index(indices, cube.coord(axis='X').shape)
    # Use only data where the standard deviation of the time series exceeds
    # 0.01 m (1 cm) this eliminates flat line model time series that come from
    # land points that should have had missing values.
    series, dist, idx = None, None, None
    for dist, idx in zip(distances, zip(i, j)):
        series = cube[(slice(None),)+idx]
        # Is it possible to get NaNs here?
        arr = ma.masked_invalid(series.data).filled(fill_value=0)
        if arr.std() >= min_var:
            break
    return series, dist, idx


def dateRange(start_date='1900-01-01', stop_date='2100-01-01',
              constraint='overlaps'):
    """Hopefully something like this will be implemented in fes soon."""
    if constraint == 'overlaps':
        propertyname = 'apiso:TempExtent_begin'
        start = fes.PropertyIsLessThanOrEqualTo(propertyname=propertyname,
                                                literal=stop_date)
        propertyname = 'apiso:TempExtent_end'
        stop = fes.PropertyIsGreaterThanOrEqualTo(propertyname=propertyname,
                                                  literal=start_date)
    elif constraint == 'within':
        propertyname = 'apiso:TempExtent_begin'
        start = fes.PropertyIsGreaterThanOrEqualTo(propertyname=propertyname,
                                                   literal=start_date)
        propertyname = 'apiso:TempExtent_end'
        stop = fes.PropertyIsLessThanOrEqualTo(propertyname=propertyname,
                                               literal=stop_date)
    return start, stop


def get_coops_longname(station):
    """Get longName for specific station from COOPS SOS using DescribeSensor
    request."""
    url = ('http://opendap.co-ops.nos.noaa.gov/ioos-dif-sos/SOS?service=SOS&'
           'request=DescribeSensor&version=1.0.0&'
           'outputFormat=text/xml;subtype="sensorML/1.0.1"&'
           'procedure=urn:ioos:station:NOAA.NOS.CO-OPS:%s') % station
    tree = etree.parse(urlopen(url))
    root = tree.getroot()
    path = "//sml:identifier[@name='longName']/sml:Term/sml:value/text()"
    namespaces = dict(sml="http://www.opengis.net/sensorML/1.0.1")
    longName = root.xpath(path, namespaces=namespaces)
    if len(longName) == 0:
        longName = station
    return longName[0]


def coops2df(collector, coops_id, sos_name):
    """Request CSV response from SOS and convert to Pandas DataFrames."""
    collector.features = [coops_id]
    collector.variables = [sos_name]
    long_name = get_coops_longname(coops_id)
    response = collector.raw(responseFormat="text/csv")
    kw = dict(parse_dates=True, index_col='date_time')
    data_df = read_csv(BytesIO(response.encode('utf-8')), **kw)
    col = 'water_surface_height_above_reference_datum (m)'
    data_df['Observed Data'] = data_df[col]
    data_df.name = long_name
    return data_df


def service_urls(records, service='odp:url'):
    """Extract service_urls of a specific type (DAP, SOS) from records."""
    service_string = 'urn:x-esri:specification:ServiceType:' + service
    urls = []
    for key, rec in records.items():
        # Create a generator object, and iterate through it until the match is
        # found if not found returns None.
        url = next((d['url'] for d in rec.references if
                    d['scheme'] == service_string), None)
        if url is not None:
            urls.append(url)
    return urls


def find_timevar(cube):
    """Return the time variable from Iris.  This is a workaround for iris
    having problems with FMRC aggregations, which produce two time
    coordinates."""
    try:
        cube.coord(axis='T').rename('time')
    except CoordinateNotFoundError:
        pass
    timevar = cube.coord('time')
    return timevar


def get_coordinates(bounding_box, bounding_box_type):
    """Create bounding box coordinates for the map."""
    coordinates = []
    if bounding_box_type is "box":
        coordinates.append([bounding_box[0][1], bounding_box[0][0]])
        coordinates.append([bounding_box[0][1], bounding_box[1][0]])
        coordinates.append([bounding_box[1][1], bounding_box[1][0]])
        coordinates.append([bounding_box[1][1], bounding_box[0][0]])
        coordinates.append([bounding_box[0][1], bounding_box[0][0]])
        return coordinates


def inline_map(m):
    """Work around to show folium in IPython notebooks."""
    m._build_map()
    srcdoc = m.HTML.replace('"', '&quot;')
    embed = HTML('<iframe srcdoc="{srcdoc}" '
                 'style="width: 100%; height: 500px; '
                 'border: none"></iframe>'.format(srcdoc=srcdoc))
    return embed
