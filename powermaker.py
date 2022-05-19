#!/usr/bin/env python3

# Importing configeration
from datetime import time
import config

# Importing supporting functions
from powermakerfunctions import *

# Importing modules
import logging # flexible event logging
import math # mathematical functions
from time import sleep  # To add delay
from numpy import interp  # To scale values
import pymysql

# Logging
logging.basicConfig(level=logging.INFO, format=f'%(asctime)s {"PROD" if config.PROD else "TEST"} %(message)s') 

conn = create_db_connection()
c = conn.cursor()
while(True):
    try:
        #get current state
        status = "unknown"
        spot_price = get_spot_price()        
        spot_price_avg, spot_price_min, spot_price_max, import_price, export_price = get_spot_price_stats()
        solar_generation = get_solar_generation()
        power_load = get_existing_load()
        cdp = is_CPD()
        battery_charge, battery_low, battery_full = get_battery_status()
        override, suggested_IE = get_override()     
        now = datetime.now().time()

        logging.info("%s - battery charging ratio" %((100-battery_charge)/100))
        # make decision based on current state
        if (override):
            #Manual override
            if (suggested_IE<0):
                status = f"Exporting - Manual Override"
                discharge_to_grid(suggested_IE)
            elif (suggested_IE>0):
                status = f"Importing - Manual Override"
                charge_from_grid(suggested_IE)
            else:
                status = f"No I/E - Manual Override"
                reset_to_default() 
        elif cdp:
            #there is CPD active so immediately go into low export state
            status = "Exporting - CPD active"
            discharge_to_grid(config.IE_MIN_RATE*-1)
    

        elif spot_price<= config.LOW_PRICE_IMPORT and not battery_full:
            #spot price less than Low price min import
            status = "Importing - Spot price < min"
            suggested_IE = config.IE_MAX_RATE
            charge_from_grid(suggested_IE)
        elif spot_price>export_price and not battery_low:
            #export power to grid if price is greater than calc export price
            status = f"Exporting - Spot Price High"
            suggested_IE = calc_discharge_rate(spot_price,export_price,spot_price_max)
            discharge_to_grid(suggested_IE)
        elif spot_price<= import_price and not battery_full:
            #import power from grid if price is less than calc export price
            status = "Importing - Spot price low"
            suggested_IE = calc_charge_rate(spot_price,import_price,spot_price_min)+power_load # move to cover existing power consumption plus import 
            charge_from_grid(suggested_IE) 

        #winter cpd dodging - charge up to 80% if spot price is <= spot price average
        elif now > time(21,0) and now < time(22,30) and battery_charge < 80 and is_CPD_period():
            logging.info("CPD CHARGING PERIOD")
            if spot_price <= spot_price_avg:
                logging.info("SPOT PRICE IS LESS THAN AVERAGE CHARGING")
                rate_to_charge = config.IE_MAX_RATE * (100-battery_charge)/100 #slow down as battery gets more full
                status = f"CPD Night Charge: {rate_to_charge}"
                charge_from_grid(rate_to_charge)
            else:
                logging.info("SPOT PRICE IS MORE AVERAGE PAUSE")
                status="CPD Night Charge: Price High"

        else: 
            #Stop any Importing or Exporting activity  
            if is_CPD_period() and spot_price <= spot_price_avg:
                suggested_IE = power_load
                if battery_charge > 50:
                    suggested_IE = suggested_IE * ((100-battery_charge)/100) #take the inverse of the battery from the grid if battery more than half full
                status = f"CPD: covering {suggested_IE}" 

            else:
                reset_to_default() 
                if battery_low:
                    status = f"No I/E - Battery Low @ {battery_charge} %"
                elif battery_full:
                    status = f"No I/E - Battery Ful @ {battery_charge} %"
                else:
                    status = f"No I/E - Battery OK @ {battery_charge} %"
        
        actual_IE = get_grid_load()
        logging.info(f"Status {status} \n" )
        c.execute(f"INSERT INTO DataPoint (SpotPrice, AvgSpotPrice, SolarGeneration , PowerLoad , BatteryCharge , Status, ActualIE, SuggestedIE) VALUES ({spot_price}, {spot_price_avg}, {solar_generation}, {power_load}, {battery_charge}, '{status}', {actual_IE}, {suggested_IE})")       

        conn.commit()

    except Exception as e:
        error = str(e)
        print (error)
        if error == "SpotPriceUnavailable":                
            status = "ERROR Spot Price Unavailable"
            logging.info(f"Status {status}" )
            c.execute(f"INSERT INTO DataPoint (SpotPrice, AvgSpotPrice, SolarGeneration , PowerLoad , BatteryCharge , Status, ActualIE, SuggestedIE) VALUES (0, 0, 0, 0, 0, '{status}', 0, 0)")
            conn.commit()
        elif error == "DatabaseUnavailable":                
            status = "Database Unavailable"
            logging.info(f"Status {status}" )
            c.execute(f"INSERT INTO DataPoint (SpotPrice, AvgSpotPrice, SolarGeneration , PowerLoad , BatteryCharge , Status, ActualIE, SuggestedIE) VALUES (0, 0, 0, 0, 0, '{status}', 0, 0)")
            conn.commit()
       
        #try and stop all I/E as an exception has occurred
        try:
            reset_to_default()
            status = "ERROR occurred I/E has been stopped"
        except Exception as e:
            error = str(e)
            status = f"ERROR unable to stop I/E: {error}"

        logging.info(f"Status {status} \n" )
        c.execute(f"INSERT INTO DataPoint (SpotPrice, AvgSpotPrice, SolarGeneration , PowerLoad , BatteryCharge , Status, ActualIE, SuggestedIE) VALUES (0, 0, 0, 0, 0, '{status}', 0, 0)")
        conn.commit()
    
    sleep(config.DELAY)
