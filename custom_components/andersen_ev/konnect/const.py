"""Constants for the Andersen Konnect API client."""

DOMAIN = "konnect"

GRAPHQL_URL = "https://graphql-mobile.andersen-ev.com"
GRAPHQL_USER_MAP_URL = "https://graphql.andersen-ev.com/get-pending-user"

API_DEVICES_URL = "https://mobile.andersen-ev.com/api/getDevices"
# The app's only mobile REST host; despite the name it serves live data, and far more of it
# than mobile.andersen-ev.com does.
API_DEVICE_URL = "https://mobile-staging.andersen-ev.com/api/getDevice"

GRAPHQL_SET_SCHEDULES_MUTATION = """
mutation setSchedules($deviceId: ID!, $scheduleSlots: ScheduleSlotsInput!) {
  setSchedules(deviceId: $deviceId, scheduleSlots: $scheduleSlots) {
    id
    name
    return_value
  }
}
"""

GRAPHQL_RUN_COMMAND_QUERY = """
mutation runAEVCommand($deviceId: ID!, $functionName: String!, $params: String) {
  runAEVCommand(deviceId: $deviceId, functionName: $functionName, params: $params) {
    return_value
    __typename
  }
}
"""

GRAPHQL_DEVICE_CHARGE_LOGS_QUERY = """
query getDeviceCalculatedChargeLogs(
    $id: ID!
    $limit: Int
    $offset: Int
    $minEnergy: Float
    $dateFrom: Date
) {
  getDevice(id: $id) {
    id
    deviceCalculatedChargeLogs(
      limit: $limit
      offset: $offset
      minEnergy: $minEnergy
      dateFrom: $dateFrom
    ) {
      chargeCostTotal
      chargeEnergyTotal
      deviceId
      duration
      gridCostTotal
      gridEnergyTotal
      particleFwVersion
      solarEnergyTotal
      solarCostTotal
      startDateTimeLocal
      surplusUsedCostTotal
      surplusUsedEnergyTotal
      uuid
      __typename
    }
    __typename
  }
}
"""

GRAPHQL_DEVICE_INFO_QUERY = """
query getDevice($id: ID!) {
  getDevice(id: $id) {
    id
    name
    last_ip_address
    deviceStatus {
      id
      evseState
      sysFwVersion
      sysSchEnabled
      sysUserLock
      sysScheduleLock
      sysRssi
      sysSSID
      sysLan
      sysTemperature
      sysFreeMemory
      sysRuntime
      sysHwVersion
      evseFwVersion
      evseHwVersion
      sysPhase
      sysVoltageA
      sysVoltageB
      sysVoltageC
      sysAmpA
      sysAmpB
      sysAmpC
      sysPowerA
      sysPowerB
      sysPowerC
      sysSolarPower
      sysGridPower
      sysChargePower
      cfgChargeAmpMin
      cfgChargeAmpMax
      scheduleSlotsArray {
        startHour
        startMinute
        endHour
        endMinute
        enabled
        dayMap {
          monday
          tuesday
          wednesday
          thursday
          friday
          saturday
          sunday
        }
      }
    }
    deviceInfo {
      id
      currency
      enableThreePhaseMultiplier
      friendlyName
      locationLongitude
      locationLatitude
      schedule0Name
      schedule1Name
      schedule2Name
      schedule3Name
      schedule4Name
      address
      addressPlace
      addressDistrict
      addressPostcode
      addressCountry
      solarOverrideStart
      notifyRcmErrorEnabled
      notifyWeeklyReportEnabled
      notifyDeviceOfflineEnabled
      purpose
      solarChargeAlways
      timeZoneRegion
      userLock
    }
  }
}
"""

GRAPHQL_DEVICE_STATUS_DETAILED_QUERY = """
query getDeviceStatus($id: ID!) {
  getDevice(id: $id) {
    name
    deviceStatus {
      id
      konnectSerial
      online
      lastEvent
      lastEventAge
      evseState
      evseResponseTime
      evseChargeAmpCurrentLimit
      sysSchEnabled
      sysUserLock
      sysScheduleLock
      sysRssi
      sysSSID
      sysLan
      sysTemperature
      sysFreeMemory
      sysRuntime
      sysFwVersion
      sysHwVersion
      evseFwVersion
      evseHwVersion
      sysBootup
      sysOs
      sysProductId
      sysProductName
      sysOtaUpdate
      sysRcmUserCleared
      sysButton
      sysFaultCode
      sysVoltageA
      sysVoltageB
      sysVoltageC
      sysAmpA
      sysAmpB
      sysAmpC
      sysPowerA
      sysPowerB
      sysPowerC
      sysPhase
      sysSolarCT
      sysGridCT
      sysAdaptiveFuse
      sysTime
      sysSch0
      sysSch1
      sysSch2
      sysSch3
      sysSch4
      cfgPENEarthConnected
      cfgDebugEnable
      cfgChargeAmpMax
      cfgChargeAmpMin
      cfgDSTActive
      sysChargingEnabled
      sysSchSet
      sysSolarPower
      sysGridPower
      sysChargePower
      sysSolarEnergyDelta
      sysGridEnergyDelta
      solarMaxGridChargePercent
      solarChargeAlways
      solarOverride
      cfgAFEnable
      cfgAFAmpMax
      cfgCTConfig
      chargeStatus {
        start
        chargeEnergyTotal
        solarEnergyTotal
        gridEnergyTotal
        chargePower
        chargePowerMax
        solarPower
        gridPower
        duration
      }
      scheduleSlotsArray {
        startHour
        startMinute
        endHour
        endMinute
        enabled
        dayMap {
          monday
          tuesday
          wednesday
          thursday
          friday
          saturday
          sunday
        }
      }
    }
  }
}
"""
