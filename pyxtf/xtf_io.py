import warnings
from collections import Iterable
from typing import Tuple, Callable, Any, Dict, Iterable

from pyxtf.xtf_ctypes import *


def xtf_padding(size: int) -> int:
    """
    Calculates the necessary padding to make the XTF packet align on a 64byte multiple.
    This padding is optional, but can improve performance.
    :param size: The number of bytes of the packet.
    :return: Padding required to align with a 64byte multiple.
    """
    return ((size + 63) // 64) * 64


def xtf_read(path: str, verbose: bool = False) -> Tuple[XTFFileHeader, Dict[XTFHeaderType, List[Any]]]:
    with open(path, 'rb') as f:
        # Read initial file header
        file_header = XTFFileHeader(buffer=f)

        n_channels = file_header.channel_count(verbose)
        if n_channels > 6:
            raise NotImplementedError("Support for more than 6 channels not implemented.")

        # Channel info
        chan_info = [file_header.ChanInfo[i] for i in range(0, n_channels)]  #type: List[XTFChanInfo]

        # Loop through XTF packets and handle according to type
        packets = {}  # type: Dict[XTFHeaderType, List[Any]]
        bathy_header_types = [
            XTFHeaderType.bathy_xyza.value,
            XTFHeaderType.bathy.value,
            XTFHeaderType.multibeam_raw_beam_angle
        ]
        while True:
            # Test for file end without advancing the file position
            if not f.peek(1):
                break

            # Save packet start location
            packet_start_loc = f.tell()

            # Read the first few shared packet bytes without advancing file pointer
            p_start = XTFPacketStart(buffer=f, file_header=file_header)
            f.seek(packet_start_loc)

            # Get the class assosciated with this header type (if any)
            # How to read and construct each type is implemented in the class (default impl. in XTFBase.__new__)
            p_headertype = XTFHeaderType(p_start.HeaderType)

            p_class = XTFPacketClasses.get(p_headertype, None)

            if p_class:
                p_header = p_class(buffer=f, file_header=file_header)
                try:
                    packets[p_headertype].append(p_header)
                except KeyError:
                    packets[p_headertype] = [p_header]
            else:
                warning_str = 'Unsupported packet type \'{}\' encountered'.format(str(p_headertype))
                warnings.warn(warning_str)

            # Skip over any data padding before next iteration
            f.seek(packet_start_loc + p_start.NumBytesThisRecord)

        return file_header, packets


def xtf_peek(path: str, properties: List[Callable[[ctypes.Structure], Any]] = None) -> List[List[Any]]:
    """
    An alternative to xtf_read, where only the properties returned through a lambda/function is stored.
    This can be used to optimize disk access for situations where partial information is needed (e.g only timestamps).
    :param path: The path to the XTF file.
    :param properties: A callable (function/lambda) that takes one of the XTF-structures and returns the data from it
    :return: A list containing the selected properties (List of lists, since multiple occurances exist for each type)
    """
    # NOTE: This should be done through the property offsets to avoid having to construct the c-structure completely
    raise NotImplementedError('xtf_peek is not implemented yet')


def concatenate_channel(pings: List[XTFPingHeader], chan_info: XTFChanInfo, channel: int) -> np.ndarray:
    """
    Concatenates the list of individual pings, and pads as necessary on the correct side to form a dense representation.
    :param chan_pings: The list of pings for one channel.
    :param chan_type: The channel info header to determine which side to pad (sidescan sonar port/stbd/other)
    :return:
    """
    # find array of largest size
    sizes = [ping.data[channel].shape[0] for ping in pings]
    min_sz, max_sz = min(sizes), max(sizes)

    # Use numpy.vstack if the sizes are all the same
    if min_sz == max_sz:
        return np.vstack([ping.data[channel] for ping in pings])
    else:
        # Get type of this channel
        chan_type = chan_info[pings[0].ping_chan_headers[channel].ChannelNumber]

        out_array = np.empty(shape=(len(pings), max_sz), dtype=pings[0].data[0].dtype)
        for i, ping in enumerate(pings[::-1]):
            sz = ping.data[channel].shape[0]
            if chan_type == XTFChannelType.stbd:
                out_array[i, :sz] = ping.data[channel]
                ping.ping_chan_headers
                out_array[i, sz:] = 0
            elif chan_type == XTFChannelType.port:
                out_array[i, :max_sz-sz] = 0
                out_array[i, max_sz-sz:] = ping.data[channel]
            else:
                # All other types: pad each side equally
                pad_div = (max_sz - sz) // 2
                remainder = 1 if (max_sz - sz) % 2 else 0
                out_array[i, :pad_div] = 0
                out_array[i, pad_div:max_sz-(pad_div+remainder)] = ping.data[channel]
                out_array[i, -pad_div:] = 0

        return out_array

if __name__ == '__main__':
    import matplotlib.pyplot as plt

    test_path = r'..\data\DemoFiles\Isis_Sonar_XTF\Reson7125.XTF'

    # Read file header and packets
    (fh, p) = xtf_read(test_path, verbose=True)

    print('The following (supported) packets are present (XTFHeaderType:count): \n\t' +
          str([key.name +':{}'.format(len(v)) for key, v in p.items()]))

    # Get multibeam (xyza) if present
    if XTFHeaderType.bathy_xyza in p:
        np_mb = [[y.fDepth for y in x.data] for x in p[XTFHeaderType.bathy_xyza]]
        np_mb = np.vstack(np_mb)
        # Transpose if the longest axis is vertical
        np_mb = np_mb if np_mb.shape[0] < np_mb.shape[1] else np_mb.T
        plt.figure()
        plt.imshow(np_mb, cmap='hot')

    # Get sonar if present
    if XTFHeaderType.sonar in p:
        upper_limit = 2 ** 14
        np_chan1 = concatenate_channel(p[XTFHeaderType.sonar], fh.ChanInfo, channel=0)
        np_chan2 = concatenate_channel(p[XTFHeaderType.sonar], fh.ChanInfo, channel=1)

        # Clip to range (max cannot be used due to outliers)
        # More robust methods are possible (through histograms / statistical outlier removal)
        np_chan1.clip(0, upper_limit-1, out=np_chan1)
        np_chan2.clip(0, upper_limit-1, out=np_chan2)

        # The sonar data is logarithmic (dB), add small value to avoid log10(0)
        np_chan1 = np.log10(np_chan1 + 0.0001)
        np_chan2 = np.log10(np_chan2 + 0.0001)

        # Transpose so that the largest axis is horizontal
        np_chan1 = np_chan1 if np_chan1.shape[0] < np_chan1.shape[1] else np_chan1.T
        np_chan2 = np_chan2 if np_chan2.shape[0] < np_chan2.shape[1] else np_chan2.T

        # The following plots the waterfall-view in separate subplots
        fig, (ax1, ax2) = plt.subplots(2, 1)
        ax1.imshow(np_chan1, cmap='gray', vmin=0, vmax=np.log10(upper_limit))
        ax2.imshow(np_chan2, cmap='gray', vmin=0, vmax=np.log10(upper_limit))

        # The following plots a signal-view of the 100th ping (in the file)
        #fig, (ax1, ax2) = plt.subplots(2,1)
        #ax1.semilogy(np.arange(0, data[0][99].shape[0]), data[0][196])
        #ax2.semilogy(np.arange(0, data[1][99].shape[0]), data[1][196])

    plt.show()



