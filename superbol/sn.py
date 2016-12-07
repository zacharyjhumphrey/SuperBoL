from pkg_resources import resource_filename, Requirement
import tables as tb
import numpy as np
from astropy import units as u
from .mag2flux import mag2flux
from .fbol import integrate_fqbol as fqbol_trapezoidal
from .fbol import ir_correction, uv_correction_linear, uv_correction_blackbody
from .fit_blackbody import bb_fit_parameters
from .fit_blackbody import bb_flux_nounits
from .luminosity import calc_Lbol
from .zip_photometry import zip_photometry
from specutils import extinction


class SN(object):
    """A supernova is the explosion that ends the life of a star

    The SN needs to be conatained within the HDF5 database before it is used
    by SNoBoL. Once there, simply create a supernova by calling the constructor
    with the name of the SN as a string of the form "sn[YEAR][Letter(s)]"

    Attributes:
        name (str): Name of the supernova, "sn" followed by the year of first 
            observation along with a letter designating the order of observation
            in that year. "sn1987a" was the first SN observed in 1987. 
            "sn2000cb" was the eightieth SN observed in 2000.

    Examples:
        An example which calculates the quasi-bolometric luminosity using
        trapezoidal integration:

        >>> my_supernova = SN('sn1998a')
        >>> my_supernova.lqbol()

        In order to coorect for unobserved flux in the UV and IR, using the
        method of Bersten & Hamuy (2009), do the following:

        >>> my_supernova.lbol_direct_bh09()

        Finally, to calculate the bolometric luminosity using bolometric
        corrections based on two-filter colors as in Bersten & Hamuy (2009):

        >>> my_supernova.lbol_bc_bh09(filter1, filter2)

        where `filter1` and `filter2` are strings designating the filter to use.
        The acceptable filter combinations at this time are limited to

        =====  =========  =========
        Color  `filter1`  `filter2`
        =====  =========  =========
        B-V    "B"        "V"
        V-I    "V"        "I"
        B-I    "B"        "I"
        =====  =========  =========
    """

    def __init__(self, name, source=None):
        """Initializes the SN with supplied value for [name]"""
        self.name = name
        self.source = source
        
        self.filter_table = None
        self.phot_table = None
        self.parameter_table = None

        self.photometry = None

        self.min_num_obs = 4

    def open_source_h5file(self):
        """Opens the hdf5 file and returns pytables File object"""
        if self.source == None:
            path_to_data = resource_filename('superbol', 'data/sn_data.h5')
            #self.filter_table = h5file.root.filters
        
            #self.sn_node = h5file.get_node('/sn', self.name)
        
            #self.phot_table = self.sn_node.phot
            #self.parameter_table = self.sn_node.parameters
        else:
            path_to_data = self.source
            
        h5file = tb.open_file(path_to_data, 'r')
        return h5file

    def import_hdf5_tables(self, h5file):
        """Reads the hdf5 file and sets up Table objects containing data"""
        sn_node = h5file.get_node('/sn', self.name)
        self.filter_table = h5file.root.filters
        self.phot_table = sn_node.phot
        self.parameter_table = sn_node.parameters

    def lbol_direct_bh09(self):
        """Calculate the bolometric lightcurve using the direct integration
        method published in Bersten & Hamuy 2009 (2009ApJ...701..200B)
        """
        h5file = self.open_source_h5file()
        self.import_hdf5_tables(h5file)

        if self.photometry == None:
            self.photometry = self.get_photometry()

        combined_phot = zip_photometry(self.photometry)

        converted_obs = self.convert_magnitudes_to_fluxes(combined_phot)
        dereddened_obs = self.deredden_fluxes(converted_obs)
        lbol_epochs = self.get_lbol_epochs(dereddened_obs, self.min_num_obs)
        distance_cm, distance_cm_err = self.get_distance_cm()
        
        dtype = [('jd', '>f8'), ('phase', '>f8'), ('phase_err', '>f8'), ('lbol', '>f8'), ('lbol_err', '>f8')]
        direct_lc = np.array([(0.0, 0.0, 0.0, 0.0, 0.0)], dtype=dtype) 
        for jd in lbol_epochs:
            names = np.array([x['name'] for x in converted_obs 
                              if x['jd'] == jd and x['name'] != b'z'])
            wavelengths = np.array([x['wavelength'] for x in converted_obs
                                    if x['jd'] == jd and x['name'] != b'z'])
            fluxes = np.array([x['flux'] for x in converted_obs
                               if x['jd'] == jd and x['name'] != b'z'])
            flux_errs = np.array([x['uncertainty'] for x in converted_obs
                                  if x['jd'] == jd and x['name'] != b'z'])

            sort_indices = np.argsort(wavelengths)
            wavelengths = wavelengths[sort_indices]
            fluxes = fluxes[sort_indices]
            flux_errs = flux_errs[sort_indices]

            fqbol, fqbol_err = fqbol_trapezoidal(wavelengths, fluxes, flux_errs)
            temperature, angular_radius, perr = bb_fit_parameters(wavelengths,
                                                                   fluxes,
                                                                   flux_errs)

            temperature_err = perr[0]
            angular_radius_err = perr[1]

            shortest_wl = np.amin(wavelengths)
            shortest_flux = np.amin(fluxes)
            shortest_flux_err = np.amin(flux_errs)
            longest_wl = np.amax(wavelengths)

            ir_corr, ir_corr_err = ir_correction(temperature,
                                                 temperature_err,
                                                 angular_radius,
                                                 angular_radius_err,
                                                 longest_wl)
            if 'U' in names:
                idx = np.nonzero(names == 'U')[0][0]
                U_flux = fluxes[idx]
                U_wl = wavelengths[idx]
                if U_flux < bb_flux_nounits(U_wl,
                                            temperature, 
                                            angular_radius):
                    uv_corr, uv_corr_err = uv_correction_linear(shortest_wl, 
                                                                shortest_flux, 
                                                                shortest_flux_err) 
                else:
                    uv_corr, uv_corr_err = uv_correction_blackbody(temperature,
                                                             temperature_err,
                                                             angular_radius,
                                                             angular_radius_err,
                                                             shortest_wl)
            else:
                uv_corr, uv_corr_err = uv_correction_blackbody(temperature,
                                                               temperature_err,
                                                               angular_radius,
                                                               angular_radius_err,
                                                               shortest_wl)

            fbol = fqbol + ir_corr + uv_corr
            fbol_err = np.sqrt(np.sum(x*x for x in [fqbol_err, ir_corr_err, uv_corr_err]))
            lum = fbol * 4.0 * np.pi * distance_cm**2.0
            lum_err = np.sqrt((4.0 * np.pi * distance_cm**2 * fbol_err)**2
                              +(8.0*np.pi * fbol * distance_cm * distance_cm_err)**2)
            phase = jd - self.parameter_table.cols.explosion_JD[0]
            phase_err = self.parameter_table.cols.explosion_JD_err[0]
            direct_lc = np.append(direct_lc, np.array([(jd, phase, phase_err, lum, lum_err)], dtype=dtype), axis=0)

        direct_lc = np.delete(direct_lc, (0), axis=0)
        h5file.close()

        return direct_lc

    def lqbol(self):
        """Calculate the quasi-bolometric lightcurve using direct integration
        with trapezoidal integration of the fluxes
        """
        h5file = self.open_source_h5file()
        self.import_hdf5_tables(h5file)

        if self.photometry == None:
            self.photometry = self.get_photometry()
        
        combined_phot = zip_photometry(self.photometry)

        converted_obs = self.convert_magnitudes_to_fluxes(combined_phot)
        dereddened_obs = self.deredden_fluxes(converted_obs)
        lbol_epochs = self.get_lbol_epochs(dereddened_obs, self.min_num_obs)
        distance_cm, distance_cm_err = self.get_distance_cm()
       

        dtype = [('jd', '>f8'), ('phase', '>f8'), ('phase_err', '>f8'), ('lbol', '>f8'), ('lbol_err', '>f8')]
        qbol_lc = np.array([(0.0, 0.0, 0.0, 0.0, 0.0)], dtype=dtype)
        
        for jd in lbol_epochs:
            wavelengths = np.array([x['wavelength'] for x in dereddened_obs
                                    if x['jd'] == jd])
            fluxes = np.array([x['flux'] for x in dereddened_obs
                               if x['jd'] == jd])
            flux_errs = np.array([x['uncertainty'] for x in dereddened_obs
                                  if x['jd'] == jd])
            names = np.array([x['name'] for x in dereddened_obs
                                  if x['jd'] == jd])

            sort_indices = np.argsort(wavelengths)
            wavelengths = wavelengths[sort_indices]
            fluxes = fluxes[sort_indices]
            flux_errs = flux_errs[sort_indices]
            names = names[sort_indices]

            fqbol, fqbol_err = fqbol_trapezoidal(wavelengths, fluxes, flux_errs)

            lqbol = fqbol * 4.0 * np.pi * distance_cm**2.0
            lqbol_err = np.sqrt((4.0 * np.pi * distance_cm**2 * fqbol_err)**2
                              +(8.0*np.pi * fqbol * distance_cm * distance_cm_err)**2)
            phase = jd - self.parameter_table.cols.explosion_JD[0]
            phase_err = self.parameter_table.cols.explosion_JD_err[0]
            # Quick and dirty fix for IR-only nights (don't want those in qbol calc)
            if min(wavelengths) < 10000.0:
                qbol_lc = np.append(qbol_lc, np.array([(jd, phase, phase_err, lqbol, lqbol_err)], dtype=dtype), axis=0)
        
        qbol_lc = np.delete(qbol_lc, (0), axis=0)
        h5file.close()

        return qbol_lc

    def lbol_bc_bh09(self, filter1, filter2):
        """Calculate the bolometric lightcurve using the bolometric corrections
        found in Bersten & Hamuy 2009 (2009ApJ...701..200B). These require 
        specifying a color, taken to be filter1 - filter2"""
        h5file = self.open_source_h5file()
        self.import_hdf5_tables(h5file)

        photometry = self.get_photometry()
        dereddened_phot = self.deredden_UBVRI_magnitudes(photometry)
        bc_epochs = self.get_bc_epochs(dereddened_phot, filter1, filter2)
        distance_cm, distance_cm_err = self.get_distance_cm()

        dtype = [('jd', '>f8'), ('phase', '>f8'), ('phase_err', '>f8'), ('lbol', '>f8'), ('lbol_err', '>f8')]
        bc_lc = np.array([(0.0, 0.0, 0.0, 0.0, 0.0)], dtype=dtype)
        
        for i in range(len(bc_epochs)):
            jd = bc_epochs[i]
            color = self.get_color(dereddened_phot, jd, filter1, filter2)
            color_err = self.get_color_uncertainty(dereddened_phot, jd, filter1, filter2) 
            v_mag = np.array([x['magnitude'] for x in dereddened_phot 
                               if x['jd'] == jd and x['name'] == b'V'])
            v_mag_err = np.array([x['uncertainty'] for x in dereddened_phot 
                                if x['jd'] == jd and x['name'] == b'V'])      
            lbol_bc, lbol_bc_err = calc_Lbol(color, color_err, filter1+"minus"+filter2, v_mag, v_mag_err, distance_cm, distance_cm_err)            
            phase = jd - self.parameter_table.cols.explosion_JD[0]
            phase_err = self.parameter_table.cols.explosion_JD_err[0]
            bc_lc = np.append(bc_lc, np.array([(jd, phase, phase_err, lbol_bc, lbol_bc_err)], dtype=dtype), axis=0)

        bc_lc = np.delete(bc_lc, (0), axis=0)
        h5file.close()

        return bc_lc

    def get_color(self, photometry, jd, filter1, filter2):
        """Get the `filter1` - `filter2` color on `jd`

        Args:
            photometry (ndarray): Numpy array of photometry from get_photometry()
            jd (float): Julian Date of the observation
            filter1 (str): Sring designation for filter 1 ("B", for example)
            filter2 (str): String designation for filter 2 ("V", for example)

        Returns:
            float: Magnitude of filter 1 minus the magnitude of filter 2.
            None: If JD not in photometry, or if filter2 or filter2 not 
                  observed on given JD
        """
        # Make sure the string matching works with Python 3
        filter1 = filter1.encode('ascii')
        filter2 = filter2.encode('ascii')

        f1_mag = None
        f2_mag = None

        for x in photometry:
            if x['jd'] == jd and x['name'] == filter1:
                f1_mag = x['magnitude']
            elif x['jd'] == jd and x['name'] == filter2:
                f2_mag = x['magnitude']

        if f1_mag == None or f2_mag == None:
            return None
        else:
            return f1_mag - f2_mag
            

    def get_color_uncertainty(self, photometry, jd, filter1, filter2):
        """Get the uncertainty of the `filter1` - `filter2` color using the quadrature sum of the uncertainties given by :math:`\\sqrt{(\\delta \\text{filter1})^2 - (\\delta \\text{filter2})^2}`

        Args:
            photometry (ndarray): Numpy array of photometry from get_photometry()
            jd (float): Julian Date of the observation
            filter1 (str): Sring designation for filter 1 ("B", for example)
            filter2 (str): String designation for filter 2 ("V", for example)

        Returns:
            float: Quadrature sum of the uncertainties in the magnitudes of
            filter 1 and filter 2.
            None: If JD not in photometry, or if filter2 or filter2 not 
                  observed on given JD

        """
        # Make sure the string matching works with Python 3
        filter1 = filter1.encode('ascii')
        filter2 = filter2.encode('ascii')

        f1_err = None
        f2_err = None

        for x in photometry:
            if x['jd'] == jd and x['name'] == filter1:
                f1_err = x['uncertainty']
            elif x['jd'] == jd and x['name'] == filter2:
                f2_err = x['uncertainty']

        if f1_err == None or f2_err == None:
            return None
        else:
            return np.sqrt(f1_err**2 + f2_err**2)


    def get_photometry(self):
        """Build a numpy array of [`jd`, `name`, `magnitude`, `uncertainty`]
        from the data contained within the HDF5 file.
        """
        dtype = [('jd', '>f8'), ('name', 'S1'), ('id', '<i4'), ('magnitude', '>f8'), ('uncertainty', '>f8')]
        photometry = np.array([(0.0,'0.0',0,0.0,0.0)], dtype=dtype)
        
        for obs in self.phot_table.iterrows():
            filterid = obs['filter_id']
            for filt in self.filter_table.where('(filter_id == filterid)'):
                photometry = np.append(photometry, 
                                       np.array([(obs['jd'], 
                                       filt['name'],
                                       filt['filter_id'],
                                       obs['magnitude'], 
                                       obs['uncertainty'])],
                                       dtype=dtype))

        photometry = np.delete(photometry, (0), axis=0)
        return photometry

    def deredden_UBVRI_magnitudes(self, photometry):
        """Apply the corrections from CCM89 (1989ApJ...345..245C), Table 3 to
        the observed photometric magnitudes.

        IMPORTANT: This will only deredden the UBVRI magnitudes at the moment"""
        self.Av_gal = self.parameter_table.cols.Av_gal[0]
        self.Av_host = self.parameter_table.cols.Av_host[0]
        self.Av_tot = self.Av_gal + self.Av_host

        ccm89_corr = {b'U': 1.569, b'B': 1.337, b'V': 1.0, b'R': 0.751, b'I': 0.479}

        for obs in photometry:
            if obs['name'] in ccm89_corr:
                obs['magnitude'] = obs['magnitude'] - ccm89_corr[obs['name']] * self.Av_tot

        return photometry

    def get_bc_epochs(self, photometry, filter1, filter2):
        """Get epochs for which observations of both filter1 and filter2 exist"""
        bc_epochs = np.array([])

        filter1 = filter1.encode('ascii')
        filter2 = filter2.encode('ascii')
        
        for jd_unique in np.unique(photometry['jd']):
            has_filter1 = False
            has_filter2 = False
            for obs in photometry:
                if obs['jd'] == jd_unique:
                    if obs['name'] == filter1:
                        has_filter1 = True
                    elif obs['name'] == filter2:
                        has_filter2 = True
            if has_filter1 and has_filter2:
                bc_epochs = np.append(bc_epochs, jd_unique)

        return bc_epochs

    def get_distance_cm(self):
        """Get the distance to the supernova in centimeters from the HDF5 file.

        Returns:
            tuple: 2-tuple
            
            * (float) distance to the supernova in cm
            * (float) uncertainty in the distance to the supernova in cm
        """
        mpc_to_cm = 3.08567758E24
        distance_cm = self.parameter_table.cols.distance_Mpc[0] * mpc_to_cm
        distance_cm_err = self.parameter_table.cols.distance_Mpc_err[0] * mpc_to_cm
        return distance_cm, distance_cm_err

    def get_lbol_epochs(self, converted_obs, min_number_obs):
        """Get only epochs with enough photometric data to calculate Lbol

        The minimum number of filters needed to calculate a luminosity is set in
        the __init__ mehod.
        """
        lbol_epochs = np.array([])
        
        for jd_unique in np.unique(converted_obs['jd']):
            num_obs = 0
            for obs in converted_obs:
                if obs['jd'] == jd_unique:
                    num_obs += 1
            if num_obs >= min_number_obs:
                lbol_epochs = np.append(lbol_epochs, jd_unique)
        
        return lbol_epochs

    def convert_magnitudes_to_fluxes(self, photometry):
        """Perform the magnitude to flux conversion.

        Creates an array of [`jd`, `name`, `wavelength`, `flux`, `uncertainty`]
        """
        dtype = [('jd', '>f8'), ('name', 'S1'), ('wavelength', '>f8'), ('flux', '>f8'), ('uncertainty', '>f8')]
        converted_obs = np.array([(0.0,'0.0',0.0,0.0,0.0)], dtype=dtype)
        
        for obs in photometry:
            filterid = obs['id']
            for filt in self.filter_table.where('(filter_id == filterid)'):
                flux, flux_err = mag2flux(obs['magnitude'], 
                                          obs['uncertainty'], 
                                          filt['eff_wl'], 
                                          filt['flux_zeropoint'])
                if 909.09 <= filt['eff_wl'] <= 33333.33:
                    converted_obs = np.append(converted_obs, 
                                                   np.array([(obs['jd'], 
                                                              filt['name'],
                                                              filt['eff_wl'],
                                                              flux, 
                                                              flux_err)],
                                                            dtype=dtype))

        converted_obs = np.delete(converted_obs, (0), axis=0)

        return converted_obs

    def deredden_fluxes(self, converted_obs):
        """Deredden the observed fluxes using the ccm89 model

        The dereddening procedure is handled by the extinction.reddening method
        from specutils.
        """
        Av_gal = self.parameter_table.cols.Av_gal[0]
        Av_host = self.parameter_table.cols.Av_host[0]
        Av_tot = Av_gal + Av_host

        for obs in converted_obs:
            obs['flux'] = obs['flux'] * extinction.reddening(obs['wavelength'] * u.AA, Av_tot, model='ccm89')

        return converted_obs

    def write_lbol_filestream(self, outfile_handle, lightcurve):
        """Write the lightcurve to a file handle"""
        np.savetxt(outfile_handle, lightcurve)

    def write_lbol_to_file(filename, lightcurve):
        """Write the lightcurve to a file on disk"""
        with open(filename, 'w') as outfile_handle:
            self.write_lbol_filestream(outfile_handle, lightcurve)
