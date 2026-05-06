import numpy as np
import matplotlib.pyplot as plt
from scipy.ndimage import rotate
import pprint

WIDTH_OFFSET = 10  # microns

# SEARCH PARAMETERS
ANGLE = 0
ANGLE_RNG = 45
ANGLE_RES = 0.1
PERIOD = 110
PERIOD_RNG = 15
PERIOD_RES = 0.2
WIDTH = 35
WIDTH_RNG = 15
WIDTH_RES = 1
PHASE = 0
PHASE_RNG = 180
PHASE_RES = 0.2

def get_projection_variance(img, angle):
    # Rotate the image
    # Note: rotate(img, angle) rotates counter-clockwise.
    # We want to find the angle that makes stripes vertical.
    # If stripes are vertical, they are constant along the y-axis (axis 1).
    rot_img = rotate(img, angle, reshape=False, order=1, mode='nearest')
    # Sum along the y-axis (axis 1 in our physicist-style (x,y) matrix)
    # The result is a profile along x.
    profile = np.mean(rot_img, axis=1)
    return np.var(profile)


def find_stripe_angle(data, display=False):
    # Load the data
    pl = data['pl'].squeeze()
    xvals = data['xvals'].squeeze()
    yvals = data['yvals'].squeeze()

    # Log scale
    log_pl = np.log10(pl + 1)  # Add 1 to avoid log(0)

    # 1. Coarse Search (10x ANGLE_RES resolution)
    coarse_res = 10 * ANGLE_RES
    coarse_angles = np.linspace(ANGLE - ANGLE_RNG, ANGLE + ANGLE_RNG, int(2 * ANGLE_RNG / coarse_res) + 1)
    coarse_variances = [get_projection_variance(log_pl, a) for a in coarse_angles]
    best_coarse = coarse_angles[np.argmax(coarse_variances)]

    # 2. Fine Search (1x ANGLE_RES resolution, sweep range = 2x coarse_res)
    fine_res = ANGLE_RES
    fine_angles = np.linspace(best_coarse - 2 * coarse_res, best_coarse + 2 * coarse_res,
                              int(4 * coarse_res / fine_res) + 1)
    fine_variances = [get_projection_variance(log_pl, a) for a in fine_angles]
    best_angle = fine_angles[np.argmax(fine_variances)]

    if display:
        # Perform the final rotation
        rotated_pl = rotate(log_pl, best_angle, reshape=False, order=3, mode='nearest')

        # Combine angles and variances for plotting
        angles = np.concatenate((coarse_angles, fine_angles))
        variances = np.concatenate((coarse_variances, fine_variances))

        # Sort and remove duplicate angles so the line plot connects properly
        angles, unique_indices = np.unique(angles, return_index=True)
        variances = variances[unique_indices]

        # Plotting
        fig, ax = plt.subplots(1, 3, figsize=(14, 4))

        # Original
        im0 = ax[0].imshow(log_pl.T, origin='lower', extent=[xvals.min(), xvals.max(), yvals.min(), yvals.max()],
                           cmap='hot')
        ax[0].set_title("Original (Log Scale)")
        ax[0].set_xlabel("x (um)")
        ax[0].set_ylabel("y (um)")
        plt.colorbar(im0, ax=ax[0])

        # Rotated
        im1 = ax[1].imshow(rotated_pl.T, origin='lower', extent=[xvals.min(), xvals.max(), yvals.min(), yvals.max()],
                           cmap='hot')
        ax[1].set_title(f"Rotated by {best_angle:.2f}° (Vertical Stripes)")
        ax[1].set_xlabel("x (um)")
        ax[1].set_ylabel("y (um)")
        plt.colorbar(im1, ax=ax[1])

        # Variance plot (adding markers to see exactly where we sampled)
        im2 = ax[2].plot(angles, variances, marker='.', linestyle='-')
        ax[2].set_title("Variance vs. Rotation Angle")
        ax[2].set_xlabel("Angle (degrees)")
        ax[2].set_ylabel("Variance of projection")
        ax[2].axvline(best_angle, color='r', linestyle='--')

        plt.tight_layout()

    return best_angle


def search_stripe_pattern(data, best_angle, display=False):
    # This function searches for the best-fitting periodic stripe pattern in the data after aligning it using the best angle.
    # The pattern is defined by three parameters: period, width of bright patches, and starting phase.

    # 1. Prepare the data profile
    log_pl = np.log10(data['pl'] + 1)  # Add 1 to avoid log(0)
    xvals = data['xvals']
    yvals = data['yvals']

    rot_pl = rotate(log_pl, best_angle, reshape=False, order=3, mode='nearest')
    profile = np.mean(rot_pl, axis=1)  # Average along the stripes to get a 1D signal
    x_axis = np.linspace(xvals.min(), xvals.max(), len(profile))

    # Normalize the profile between 0 and 1 for easier comparison
    p_min, p_max = np.percentile(profile, [5, 95])
    norm_profile = np.clip((profile - p_min) / (p_max - p_min), 0, 1)

    # Helper function to evaluate a 3D grid of parameters (Vectorized over Phase)
    def evaluate_grid(periods, widths, phases):
        # Initialize to -infinity so negative scores still register as a valid maximum
        best_score = -np.inf

        # Default to the center of the search grid (which is the coarse-search best for fine sweeps)
        best_params = (periods[len(periods) // 2], widths[len(widths) // 2], phases[len(phases) // 2])

        # Prepare 2D broadcastable arrays to eliminate the phase loop
        x_row = x_axis[np.newaxis, :]  # Shape: (1, N)
        phases_col = phases[:, np.newaxis]  # Shape: (Ph, 1)

        for p in periods:
            # Computes shifts for all phases at once! Shape: (Ph, N)
            phase_shift = (x_row - phases_col) % p

            for w in widths:
                synth = (phase_shift < w).astype(float)

                # Vectorized scoring using fast matrix multiplication
                # This evaluates the score for EVERY phase simultaneously
                scores = np.dot(synth, norm_profile) - 0.5 * np.sum(synth, axis=1)

                max_idx = np.argmax(scores)
                if scores[max_idx] > best_score:
                    best_score = scores[max_idx]
                    best_params = (p, w, phases[max_idx])

        return best_params, best_score

    # 2. Coarse 3D Search (10x resolution)
    coarse_p_res = 10 * PERIOD_RES
    coarse_w_res = 10 * WIDTH_RES
    coarse_phi_res = 10 * PHASE_RES

    coarse_periods = np.linspace(PERIOD - PERIOD_RNG, PERIOD + PERIOD_RNG, int(2 * PERIOD_RNG / coarse_p_res) + 1)
    coarse_widths = np.linspace(WIDTH - WIDTH_RNG, WIDTH + WIDTH_RNG, int(2 * WIDTH_RNG / coarse_w_res) + 1)
    coarse_phases = np.linspace(PHASE - PHASE_RNG, PHASE + PHASE_RNG, int(2 * PHASE_RNG / coarse_phi_res) + 1)

    best_coarse_params, _ = evaluate_grid(coarse_periods, coarse_widths, coarse_phases)
    best_c_p, best_c_w, best_c_phi = best_coarse_params

    # 3. Fine 3D Search (1x resolution, sweep range = 2x coarse_res)
    fine_periods = np.linspace(best_c_p - 2 * coarse_p_res, best_c_p + 2 * coarse_p_res,
                               int(4 * coarse_p_res / PERIOD_RES) + 1)
    fine_widths = np.linspace(best_c_w - 2 * coarse_w_res, best_c_w + 2 * coarse_w_res,
                              int(4 * coarse_w_res / WIDTH_RES) + 1)
    fine_phases = np.linspace(best_c_phi - 2 * coarse_phi_res, best_c_phi + 2 * coarse_phi_res,
                              int(4 * coarse_phi_res / PHASE_RES) + 1)

    params, _ = evaluate_grid(fine_periods, fine_widths, fine_phases)
    period_best, width_best, phase_best = params

    if display:
        print(f"Best Params - Period: {period_best}, Width: {width_best}, Phase: {phase_best}")

        # Optimized parameters
        theta_deg = best_angle  # Rotate back by the negative of the best angle to align with original coordinates
        theta_rad = np.radians(theta_deg)
        period = period_best
        width_bright = width_best
        phase_offset = phase_best

        # 4. Coordinate Transformation (Physicist style [x, y])
        X, Y = np.meshgrid(xvals, yvals, indexing='ij')

        # Transform original coordinates to the rotated frame
        X_prime = X * np.cos(theta_rad) - Y * np.sin(theta_rad)

        # 5. Generate the Mask
        mask = ((X_prime - phase_offset) % period < width_bright).astype(float)

        # 6. Output
        plt.figure(figsize=(8, 8))
        # plt.imshow(mask.T, origin='lower', extent=[xvals.min(), xvals.max(), yvals.min(), yvals.max()], cmap='gray')
        plt.plot(norm_profile)
        synth = ((x_axis - phase_best) % period_best < width_best).astype(float)
        plt.plot(synth)
        plt.title("Final Synthetic Black and White Mask")
        plt.axis('on')

    return params

def generate_stripe_mask(data, theta_deg, period, width_bright, phase_offset, display=False):
    """
    Generates a set of 5 binary masks representing different spatial zones of the
    periodic stripe pattern relative to the fitted 'land' area.

    Returns (tuple of numpy.ndarrays):
    ----------------------------------
    0: mask (Fit)      - The baseline 'land' area calculated directly from the fit.
    1: mask1 (Core)    - The innermost 'safe' land area (fit area minus width_offset).
    2: mask2 (Shoulder)- The inner edge of the land; pixels still within the fit
                         but near the boundary.
    3: mask3 (Fringe)  - The outer edge immediately adjacent to the land; pixels
                         just outside the fit within the offset distance.
    4: mask4 (BG)      - The 'safe' background area; regions definitely outside
                         the land and its fringe.
    """

    pl = data['pl'].squeeze()
    xvals = data['xvals'].squeeze()
    yvals = data['yvals'].squeeze()

    # --- COORDINATE TRANSFORMATION ---
    theta_rad = np.radians(theta_deg)

    # 1. OPTIMIZATION: Avoid full meshgrid allocation using broadcasting
    # xvals is (N,), yvals is (M,). X_prime becomes (N, M) automatically
    x_proj = xvals[:, np.newaxis] * np.cos(theta_rad)
    y_proj = yvals[np.newaxis, :] * np.sin(theta_rad)
    X_prime = x_proj - y_proj

    # --- GENERATE MASK ---
    # 2. OPTIMIZATION & BUGFIX: We keep these as boolean arrays (True/False)
    # Boolean arrays are much faster for bitwise logic (&, ~) than integer arithmetic.
    # More importantly, keeping them boolean ensures `pl[mask]` works correctly downstream!
    mask = ((X_prime - phase_offset) % period < width_bright)

    # #1 - inside land
    effective_width = width_bright + 2 * -WIDTH_OFFSET
    effective_phase = phase_offset - (2 * -WIDTH_OFFSET / 2.0)
    mask1 = ((X_prime - effective_phase) % period <= effective_width)

    # #4 - outside land
    effective_width = width_bright + 2 * WIDTH_OFFSET
    effective_phase = phase_offset - (2 * WIDTH_OFFSET / 2.0)
    mask4 = ((X_prime - effective_phase) % period >= effective_width)

    # #2 - edge inner
    mask2 = mask & ~mask1

    # #3 - edge outer
    mask3 = ~mask & ~mask4

    # --- PLOT RESULTS ---
    if display:
        # 3. OPTIMIZATION: Log scale computation is expensive.
        # By moving it inside the display block, we skip it entirely when display=False
        log_pl = np.log10(pl + 1)

        fig, ax = plt.subplots(1, 2, figsize=(14, 6))

        # Left: Overlay for confirmation
        # (We cast masks to int here just for matplotlib's contour function)
        ax[0].imshow(log_pl.T, origin='lower', extent=[xvals.min(), xvals.max(), yvals.min(), yvals.max()],
                     cmap='hot')
        ax[0].contour(mask.astype(int).T, levels=[0.5], colors='blue', linestyles='dashed', linewidths=1.0,
                      extent=[xvals.min(), xvals.max(), yvals.min(), yvals.max()])
        ax[0].contour(mask1.astype(int).T, levels=[0.5], colors='blue', linewidths=1.0,
                      extent=[xvals.min(), xvals.max(), yvals.min(), yvals.max()])
        ax[0].contour(mask4.astype(int).T, levels=[0.5], colors='blue', linewidths=1.0,
                      extent=[xvals.min(), xvals.max(), yvals.min(), yvals.max()])
        ax[0].set_title(f"Fit Overlay (Offset: {WIDTH_OFFSET})")
        ax[0].set_xlabel("x")
        ax[0].set_ylabel("y")

        # Right: The Black & White Mask
        ax[1].imshow(mask.astype(int).T, origin='lower',
                     extent=[xvals.min(), xvals.max(), yvals.min(), yvals.max()], cmap='gray')
        ax[1].set_title("Generated B&W Mask")
        ax[1].axis('off')

        plt.tight_layout()
        plt.show()

    return mask.astype(int), mask1.astype(int), mask2.astype(int), mask3.astype(int), mask4.astype(int)


def analyze_image(data, masks):
    pl = data['pl']
    # todo: preprocess image / clip intensity / etc

    metrics = {}
    metrics['total pixels'] = pl.size
    metrics['mean (cps)'] = np.mean(pl)
    metrics['stdev (cps)'] = np.std(pl)

    for i in [1, 2, 3, 4]:
        area_pixels = np.count_nonzero(masks[i])
        metrics[f'area {i} pixels'] = area_pixels
        metrics[f'area {i} percent'] = (area_pixels / pl.size) * 100

        # Safely handle empty masks to avoid numpy NaN warnings
        if area_pixels > 0:
            metrics[f'area {i} mean (cps)'] = np.mean(pl[masks[i]])
            metrics[f'area {i} stdev (cps)'] = np.std(pl[masks[i]])
        else:
            metrics[f'area {i} mean (cps)'] = 0.0
            metrics[f'area {i} stdev (cps)'] = 0.0

    pprint.pprint(metrics)