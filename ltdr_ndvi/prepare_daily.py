"""
for use with NDVI product from LTDR raw dataset

- Prepares list of all files
- Builds list of day files to process
- Processes day files
- Builds list of day files to aggregate to months
- Run month aggregation
- Builds list of month files to aggregate to years
- Run year aggregation

example LTDR product file names (ndvi product code is AVH13C1)

AVH13C1.A1981181.N07.004.2013227210959.hdf

split file name by "."
eg:

full file name - "AVH13C1.A1981181.N07.004.2013227210959.hdf"

0     product code        AVH13C1
1     date of image       A1981181
2     sensor code         N07
3     misc                004
4     processed date      2013227210959
5     extension           hdf

"""

import os
import errno
from collections import OrderedDict
from datetime import datetime

import rasterio
import numpy as np
import pandas as pd
from osgeo import gdal, osr


mode = "auto"


build_list = [
    "daily",
    "monthly",
    "yearly"
]


src_base = "/sciclone/aiddata10/REU/geo/raw/ltdr/LAADS/465"

dst_base = "/sciclone/aiddata10/REU/geo/data/rasters/ltdr/avhrr_ndvi_v5"


# filter options to accept/deny based on sensor, year
# all values must be strings
# do not enable/use both accept/deny for a given field

filter_options = {
    'use_sensor_accept': False,
    'sensor_accept': [],
    'use_sensor_deny': False,
    'sensor_deny': [],
    'use_year_accept': False,
    'year_accept': ['1987'],
    'use_year_deny': True,
    'year_deny': ['2019']
}


# -----------------------------------------------------------------------------


def build_data_list(input_base, output_base, ops):
    f = []
    for root, dirs, files in os.walk(input_base):
        for file in files:
            if file.endswith(".hdf"):
                f.append(os.path.join(root, file))
    df_dict_list = []
    for input_path in f:
        items = os.path.basename(input_path).split(".")
        year = items[1][1:5]
        day = items[1][5:8]
        sensor = items[2]
        month = "{0:02d}".format(
            datetime.strptime("{0}+{1}".format(year, day), "%Y+%j").month)
        output_path = os.path.join(
            output_base, "daily/avhrr_ndvi_v5_{}_{}_{}.tif".format(sensor, year, day)
        )
        df_dict_list.append({
            "input_path": input_path,
            "sensor": sensor,
            "year": year,
            "month": month,
            "day": day,
            "year_month": year+"_"+month,
            "year_day": year+"_"+day,
            "output_path": output_path
        })
    df = pd.DataFrame(df_dict_list)
    df = df.sort(["input_path"])
    # df = df.drop_duplicates(subset="year_day", take_last=True)
    sensors = sorted(list(set(df["sensor"])))
    years = sorted(list(set(df["year"])))
    filter_sensors = None
    if ops['use_sensor_accept']:
        filter_sensors = [i for i in sensors if i in ops['sensor_accept']]
    elif ops['use_sensor_deny']:
        filter_sensors = [i for i in sensors if i not in ops['sensor_deny']]
    if filter_sensors:
        df = df.loc[df["sensor"].isin(filter_sensors)]
    filter_years = None
    if ops['use_year_accept']:
        filter_years = [i for i in years if i in ops['year_accept']]
    elif ops['use_year_deny']:
        filter_years = [i for i in years if i not in ops['year_deny']]
    if filter_years:
        df = df.loc[df["year"].isin(filter_years)]
    return df


def prep_daily_data(task):
    src, dst = task
    year = os.path.basename(src).split(".")[1][1:5]
    day = os.path.basename(src).split(".")[1][5:8]
    sensor = os.path.basename(src).split(".")[2]
    print "Processing Day {} {} {}".format(sensor, year, day)
    process_daily_data(src, dst)


def prep_monthly_data(task):
    year_month, month_files, month_path = task
    print "Processing Month {}".format(year_month)
    data, meta = aggregate_rasters(file_list=month_files, method="max")
    write_raster(month_path, data, meta)


def prep_yearly_data(task):
    year, year_files, year_path = task
    print "Processing Year {}".format(year)
    data, meta = aggregate_rasters(file_list=year_files, method="mean")
    write_raster(year_path, data, meta)


def process_daily_data(input_path, output_path):
    """Process input raster and create output in output directory

    Unpack NDVI subdataset from a HDF container
    Reproject to EPSG:4326
    Set values <0 (other than nodata) to 0
    Write to GeoTiff

    Parts of code pulled from:

    https://gis.stackexchange.com/questions/174017/extract-scientific-layers-from-modis-hdf-dataeset-using-python-gdal
    https://gis.stackexchange.com/questions/42584/how-to-call-gdal-translate-from-python-code
    https://stackoverflow.com/questions/10454316/how-to-project-and-resample-a-grid-to-match-another-grid-with-gdal-python/10538634#10538634
    https://jgomezdans.github.io/gdal_notes/reprojection.html

    Notes:

    Rebuilding geotransform is not really necessary in this case but might
    be useful for future data prep scripts that can use this as startng point.

    """
    # open the dataset and subdataset
    hdf_ds = gdal.Open(input_path, gdal.GA_ReadOnly)

    layers = hdf_ds.GetSubDatasets()

    # ndvi
    ndvi_ds = gdal.Open(layers[0][0], gdal.GA_ReadOnly)
    # qa
    qa_ds = gdal.Open(layers[1][0], gdal.GA_ReadOnly)

    # clean data
    ndvi_array = ndvi_ds.ReadAsArray().astype(np.int16)

    qa_array = qa_ds.ReadAsArray().astype(np.int16)


    binary_repr_v = np.vectorize(np.binary_repr)


    flag = lambda i: bool(int(max(np.array(list(i))[qa_mask_vals])))
    flag_v = np.vectorize(flag)


    # list of qa fields and bit numbers
    # https://ltdr.modaps.eosdis.nasa.gov/ltdr/docs/AVHRR_LTDR_V5_Document.pdf
    # MSB first (invert for Python list lookip)

    qa_bits = {
        15: "Polar flag: latitude > 60deg (land) or > 50deg (ocean)",
        14: "BRDF-correction issues",
        13: "RHO3 value is invalid",
        12: "Channel 5 value is invalid",
        11: "Channel 4 value is invalid",
        10: "Channel 3 value is invalid",
        9: "Channel 2 (NIR) value is invalid",
        8: "Channel 1 (visible) value is invalid",
        7: "Channel 1-5 are invalid",
        6: "Pixel is at night (high solar zenith angle)",
        5: "Pixel is over dense dark vegetation",
        4: "Pixel is over sun glint",
        3: "Pixel is over water",
        2: "Pixel contains cloud shadow",
        1: "Pixel is cloudy",
        0: "Unused"
    }

    qa_bin_array = binary_repr_v(qa_array, width=16)

    # qa_mask_vals = [15, 9, 8, 6, 4, 3, 2, 1]
    qa_mask_vals = [15, 9, 8, 1]

    # convert bit number to array index
    qa_mask_vals = [abs(x - 15) for x in qa_mask_vals]


    qa_mask = flag_v(qa_bin_array)

    ndvi_array[qa_mask] = -9999

    ndvi_array[np.where((ndvi_array < 0) & (ndvi_array > -9999))] = 0
    ndvi_array[np.where(ndvi_array > 10000)] = 10000

    # -----------------

    # prep projections and transformations
    src_proj = osr.SpatialReference()
    src_proj.ImportFromWkt(ndvi_ds.GetProjection())

    dst_proj = osr.SpatialReference()
    dst_proj.ImportFromEPSG(4326)

    tx = osr.CoordinateTransformation(src_proj, dst_proj)

    src_gt = ndvi_ds.GetGeoTransform()
    pixel_xsize = src_gt[1]
    pixel_ysize = abs(src_gt[5])

    # extents
    (ulx, uly, ulz ) = tx.TransformPoint(src_gt[0], src_gt[3])

    (lrx, lry, lrz ) = tx.TransformPoint(
        src_gt[0] + src_gt[1]*ndvi_ds.RasterXSize,
        src_gt[3] + src_gt[5]*ndvi_ds.RasterYSize)

    # new geotransform
    dst_gt = (ulx, pixel_xsize, src_gt[2],
                uly, src_gt[4], -pixel_ysize)

    # -----------------

    # create new raster
    driver = gdal.GetDriverByName('GTiff')
    out_ds = driver.Create(
        output_path,
        int((lrx - ulx)/pixel_xsize),
        int((uly - lry)/pixel_ysize),
        1,
        gdal.GDT_Int16
    )

    # set transform and projection
    out_ds.SetGeoTransform(dst_gt)
    out_ds.SetProjection(dst_proj.ExportToWkt())

    out_band = out_ds.GetRasterBand(1)
    out_band.WriteArray(ndvi_array)
    out_band.SetNoDataValue(-9999)

    # complete write
    out_ds = None

    # close out datasets
    hdf_ds = None
    ndvi_ds = None



def aggregate_rasters(file_list, method="mean"):
    """Aggregate multiple rasters

    Aggregates multiple rasters with same features (dimensions, transform,
    pixel size, etc.) and creates single layer using aggregation method
    specified.

    Supported methods: mean (default), max, min, sum

    Arguments
        file_list (list): list of file paths for rasters to be aggregated
        method (str): method used for aggregation

    Return
        result: rasterio Raster instance
    """
    store = None
    for ix, file_path in enumerate(file_list):

        try:
            raster = rasterio.open(file_path)
        except:
            print "Could not include file in aggregation ({0})".format(file_path)
            continue

        active = raster.read(masked=True)

        if store is None:
            store = active.copy()

        else:
            # make sure dimensions match
            if active.shape != store.shape:
                raise Exception("Dimensions of rasters do not match")

            if method == "max":
                store = np.ma.array((store, active)).max(axis=0)

                # non masked array alternatives
                # store = np.maximum.reduce([store, active])
                # store = np.vstack([store, active]).max(axis=0)

            elif method == "mean":
                if ix == 1:
                    weights = (~store.mask).astype(int)

                store = np.ma.average(np.ma.array((store, active)), axis=0, weights=[weights, (~active.mask).astype(int)])
                weights += (~active.mask).astype(int)

            elif method == "min":
                store = np.ma.array((store, active)).min(axis=0)

            elif method == "sum":
                store = np.ma.array((store, active)).sum(axis=0)

            else:
                raise Exception("Invalid method")

    store = store.filled(raster.nodata)
    return store, raster.profile


def write_raster(path, data, meta):
    make_dir(os.path.dirname(path))
    meta['dtype'] = data.dtype
    with rasterio.open(path, 'w', **meta) as result:
        try:
            result.write(data)
        except:
            print path
            print meta
            print data.shape
            raise


def make_dir(path):
    try:
        os.makedirs(path)
    except OSError as exception:
        if exception.errno != errno.EEXIST:
            raise


def run(tasks, func, mode="auto"):
    parallel = False
    size = 1
    rank = 0
    if mode in ["auto", "parallel"]:
        try:
            from mpi4py import MPI
            parallel = True
            comm = MPI.COMM_WORLD
            size = comm.Get_size()
            rank = comm.Get_rank()
        except:
            if mode == "parallel":
                raise Exception("Failed to run job in parallel")
    elif mode != "serial":
        raise Exception("Invalid `mode` value for script.")
    c = rank
    while c < len(tasks):
        try:
            func(tasks[c])
        except Exception as e:
            print "Error processing: {0}".format(tasks[c])
            # raise
            print e
        c += size
    if parallel:
        comm.Barrier()


# -----------------------------------------------------------------------------


# build day dataframe
day_df = build_data_list(src_base, dst_base, filter_options)


# build month dataframe
month_df = day_df[["output_path", "year", "year_month"]].groupby("year_month", as_index=False).aggregate({
    "output_path": [lambda x: tuple(x), "count"],
    "year": "last"
})
month_df.columns = ["year_month", "day_path_list", "count", "year"]

minimum_days_in_month = 20
month_df = month_df.loc[month_df["count"] >= minimum_days_in_month]

month_df["output_path"] = month_df.apply(
    lambda x: os.path.join(dst_base, "monthly/avhrr_ndvi_v5_{}.tif".format(x["year_month"])), axis=1
)


# build year dataframe
year_df = month_df[["output_path", "year"]].groupby("year", as_index=False).aggregate({
    "output_path": [lambda x: tuple(x), "count"]
})
year_df.columns = ["year", "month_path_list", "count"]

year_df["output_path"] = year_df["year"].apply(
    lambda x: os.path.join(dst_base, "yearly/avhrr_ndvi_v5_{}.tif".format(x))
)


day_qlist = []
for _, row in day_df.iterrows():
    day_qlist.append([row["input_path"], row["output_path"]])

month_qlist = []
for _, row in month_df.iterrows():
    month_qlist.append([row["year_month"], row["day_path_list"], row["output_path"]])

year_qlist = []
for _, row in year_df.iterrows():
    year_qlist.append([row["year"], row["month_path_list"], row["output_path"]])


if "daily" in build_list:
    make_dir(os.path.join(dst_base, "daily"))
    run(day_qlist, prep_daily_data, mode=mode)

if "monthly" in build_list:
    make_dir(os.path.join(dst_base, "monthly"))
    run(month_qlist, prep_monthly_data, mode=mode)

if "yearly" in build_list:
    make_dir(os.path.join(dst_base, "yearly"))
    run(year_qlist, prep_yearly_data, mode=mode)
